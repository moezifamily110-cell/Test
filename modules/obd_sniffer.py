# -*- coding: utf-8 -*-
"""
OBD Sniffer - ضبط ترافیک زنده از خودرو (اختیاری).

این ماژول برای اتصال به دستگاه ELM327 و ضبط پاسخ‌های واقعی ECU طراحی شده است.
خروجی آن می‌تواند برای تأیید فرمول‌ها و دستورات استخراج‌شده از منابع دیگر استفاده شود.

هشدار: این ماژول نیاز به سخت‌افزار ELM327 واقعی دارد. در صورت نبود،
تنها شبیه‌سازی انجام می‌شود و داده واقعی تولید نمی‌شود.
"""
import time
from typing import Optional


class OBDSniffer:
    def __init__(self, port: str = 'COM3', baudrate: int = 38400, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None
        self._connected = False
        self._simulation = False

    def connect(self) -> bool:
        """اتصال به دستگاه ELM327. در صورت نبود پورت، حالت شبیه‌سازی فعال می‌شود."""
        try:
            import serial
            self.ser = serial.Serial(
                self.port, self.baudrate, timeout=self.timeout,
                bytesize=8, parity='N', stopbits=1,
            )
            # ریست دستگاه
            self._send_cmd(b'AT Z\r')
            time.sleep(0.5)
            self._send_cmd(b'AT E0\r')   # خاموش کردن echo
            time.sleep(0.1)
            self._send_cmd(b'AT L0\r')   # خاموش کردن خط‌خورد
            time.sleep(0.1)
            self._send_cmd(b'AT S0\r')   # خاموش کردن فاصله
            time.sleep(0.1)
            self._connected = True
            print(f"[+] Connected to OBD Sniffer on {self.port}")
            return True
        except ImportError:
            print("[!] pyserial نصب نیست؛ pip install pyserial")
            return False
        except Exception as e:
            print(f"[!] اتصال به OBD ناموفق ({e}); فعال‌سازی حالت شبیه‌سازی")
            self._simulation = True
            self._connected = True
            return True

    def _send_cmd(self, cmd: bytes) -> str:
        if self._simulation:
            return self._simulate_response(cmd)
        if not self.ser or not self.ser.is_open:
            return ""
        self.ser.write(cmd)
        time.sleep(0.1)
        response = self.ser.read_all().decode('ascii', errors='ignore')
        return response.strip()

    def _simulate_response(self, cmd: bytes) -> str:
        """پاسخ شبیه‌سازی‌شده برای تست بدون سخت‌افزار واقعی."""
        cmd_str = cmd.decode('ascii', errors='ignore').strip()
        if cmd_str.startswith('AT'):
            return 'OK'
        # شبیه‌سازی پاسخ OBD-II ساده
        if '0100' in cmd_str:
            return '41 00 BE 1F B8 10'
        if '010C' in cmd_str:  # RPM
            return '41 0C 1A F8'
        if '010D' in cmd_str:  # Speed
            return '41 0D 41'
        if '0105' in cmd_str:  # Coolant temp
            return '41 05 3F'
        return 'NO DATA'

    def sniff_pid(self, pid_command: str) -> str:
        """ارسال دستور PID و دریافت پاسخ."""
        if not self._connected:
            return ""
        cmd = f"{pid_command}\r".encode('ascii')
        return self._send_cmd(cmd)

    def sniff_multiple(self, pids: list, delay: float = 0.2) -> dict:
        """ضبط چندین PID متوالی."""
        results = {}
        for pid in pids:
            resp = self.sniff_pid(pid)
            results[pid] = resp
            time.sleep(delay)
        return results

    def verify_formula(self, pid_command: str, formula: str,
                        expected_value: Optional[float] = None) -> dict:
        """تأیید یک فرمول با پاسخ واقعی ECU."""
        resp = self.sniff_pid(pid_command)
        result = {
            "pid": pid_command,
            "raw_response": resp,
            "formula": formula,
            "status": "unverified",
            "calculated_value": None,
            "expected_value": expected_value,
        }

        if not resp or resp in ("NO DATA", "?", ""):
            result["status"] = "no_response"
            return result

        try:
            from modules.sicar_formula import evaluate
            eval_result = evaluate(formula, resp, ctype=2, decimals=2)
            result["calculated_value"] = eval_result.value
            result["eval_status"] = eval_result.status
            result["eval_note"] = eval_result.note

            if expected_value is not None and eval_result.value is not None:
                try:
                    diff = abs(float(eval_result.value) - expected_value)
                    result["difference"] = diff
                    result["status"] = "verified" if diff < 0.5 else "mismatch"
                except (ValueError, TypeError):
                    result["status"] = "parse_error"
            else:
                result["status"] = "captured"
        except Exception as e:
            result["status"] = f"error: {e}"

        return result

    def close(self):
        """بستن اتصال."""
        if self.ser and self.ser.is_open:
            self.ser.close()
        self._connected = False
        self._simulation = False
