# -*- coding: utf-8 -*-
"""
موتور مرجع فرمول سیکار (پورت وفادار مسیر زنده Run_Request.py مخزن Sisoog/sicar-diag)
مرجع: https://github.com/Sisoog/sicar-diag/blob/main/Run_Request.py
  - make_expression / customGetEndBTagIndex / customGetValueExp
  - customCalc_formula / calcFromCondition
  - شاخه‌بندی ctype در حلقه اجرای فرمان
و app_Info.py (getByteWithLen_1، HexToBinary، HexToBinaryPosition، roundDouble).

قواعد:
  * هیچ معنای «ریاضی استاندارد» جایگزین رفتار سورس نمی‌شود؛ خروجی همان چیزی است که سیکار محاسبه می‌کند.
  * جاهایی که سورس متن فرمول را نادیده می‌گیرد یا خطا می‌دهد، با وضعیت مشخص گزارش می‌شوند (نه مقدار ساختگی).
  * به‌جای eval خام، یک ارزیاب AST محدود استفاده شده.
  * همه تابع‌ها خالص‌اند و روی داده محلی کار می‌کنند.
"""
from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# ۱) جدول نوع فرمان (CommandType.py مخزن سیکار)
# ---------------------------------------------------------------------------
CTYPE_NAMES = {
    0: "cmd_noCmd", 1: "cmd_InitCommunication", 2: "cmd_Param_2", 3: "cmd_Operation", 4: "cmd_Trouble",
    5: "cmd_ClearTrouble", 6: "cmd_ConnectToDevice_ELM", 7: "cmd_ConnectToDevice_ASAN",
    8: "cmd_CountTroubleCode_08", 9: "cmd_ReadChar_09", 10: "cmd_EndOperation_10",
    11: "cmd_PramReplaceText_11", 12: "cmd_ReadHex_12", 13: "cmd_AllDecimal_13",
    14: "cmd_GetValue_Configuration_14", 15: "cmd_SetValue_Configuration_15", 16: "cmd_PendingTrouble_16",
    17: "cmd_GroupLoop_17", 18: "cmd_ReInit", 19: "cmd_ReadFreezeFrame_19", 20: "cmd_Stop_Communication_20",
    21: "cmd_SingleLoop_21", 22: "cmd_TextStr", 23: "cmd_wakeup", 24: "cmd_Trouble_No_OBD2_CAN",
    25: "cmd_ReInit_Communication", 26: "cmd_Trouble_NoObd2", 27: "cmd_LoopUnlimited_27",
    28: "cmd_SingleDecimal_28", 29: "cmd_MessageBox_29", 30: "cmd_Trouble_ReverseCode",
    31: "cmd_Trouble_NoOby2_NoFaultCode", 32: "cmd_AvgParam", 33: "cmd_ReadAlphabetWithIndex_33",
    34: "cmd_LoopCmdToExit_34", 35: "cmd_inputBox_35", 36: "cmd_Opr_IgnoreResp_36", 37: "cmd_CloseSession_37",
    38: "cmd_findFirstValidResultInGroup", 39: "cmd_Opr_RespIsImportant_39", 40: "cmd_MapTable",
    41: "cmd_SplitValueToCommands", 42: "cmd_Condition_42", 43: "cmd_SubGroupInOperation", 44: "cmd_BatchCommand",
}

CALC_CTYPES = {2, 13, 14, 32}          # calc_formula(make_expression(...))
EXPR_ONLY_CTYPES = {9, 28, 33, 42}     # فقط make_expression
TROUBLE_CTYPES = {4, 8, 16, 24, 26, 30, 31}

# وکتور تست استاندارد برای ارزیابی زمان اجرای فرمول‌ها (بدون اتصال به خودرو واقعی)
# ۱۶ بایت تصادفی اما ثابت برای سازگاری بین اجراها
TEST_VECTOR = "41 0C 1A F8 41 0D 41 0F 41 10 41 11 41 05 3F 41 06 8A"

# ---------------------------------------------------------------------------
# ۲) جدول پروتکل ELM327
# ---------------------------------------------------------------------------
ELM_PROTOCOLS = {
    "1": ("SAE J1850 PWM", "41600", "J1850"),
    "2": ("SAE J1850 VPW", "10400", "J1850"),
    "3": ("ISO 9141-2 (5 baud init)", "10400", "K-Line"),
    "4": ("ISO 14230-4 KWP (5 baud init)", "10400", "K-Line"),
    "5": ("ISO 14230-4 KWP (fast init)", "10400", "K-Line"),
    "6": ("ISO 15765-4 CAN (11 bit ID)", "500000", "CAN"),
    "7": ("ISO 15765-4 CAN (29 bit ID)", "500000", "CAN"),
    "8": ("ISO 15765-4 CAN (11 bit ID)", "250000", "CAN"),
    "9": ("ISO 15765-4 CAN (29 bit ID)", "250000", "CAN"),
    "10": ("SAE J1939 CAN (29 bit ID)", "250000", "CAN"),
    "A": ("SAE J1939 CAN (29 bit ID)", "250000", "CAN"),
}


def protocol_from_baudtype(baudtype) -> Tuple[str, str, str]:
    """(protocol, baud_rate, note) — فقط اگر baudtype در جدول باشد."""
    bt = str(baudtype).strip() if baudtype is not None else ""
    key = bt.upper() if bt.upper() == "A" else bt
    p = ELM_PROTOCOLS.get(key)
    if not p:
        return "", "", f"baudtype={bt!r} در جدول ELM327 نیست؛ پروتکل ثبت نشد"
    return (f"{p[2]}: {p[0]} [ELM327 AT SP {bt}]", p[1],
            f"پروتکل از baudtype={bt} (سورس سیکار app_Info.py: \"AT SP\"+baudType؛ جدول پروتکل دیتاشیت ELM327)")


def parse_ctype(value) -> Optional[int]:
    """'2 cmd_Param_2' یا 2 یا '2' → 2"""
    if value is None:
        return None
    m = re.match(r"\s*(\d+)", str(value))
    return int(m.group(1)) if m else None


def decimals_from_notes(notes: str) -> int:
    """استخراج decimalp از یادداشت‌ها: 'decimalp=2' → 2"""
    if not notes:
        return 0
    m = re.search(r"decimalp\s*=\s*(\d+)", notes)
    return int(m.group(1)) if m else 0

# ---------------------------------------------------------------------------
# ۳) تحلیل ایستای فرمول (پورت analyzeFormula در main.go نسخه ۱۵)
# ---------------------------------------------------------------------------
_RE_BYTE = re.compile(r"B\[(\d+)(?:~(\d+))?\]")
_RE_BITFLAG = re.compile(r"B\[(\d+)\]\s*&\s*\(\s*1\s*<<\s*(\d+)\s*\)")
_RE_BITMASK = re.compile(r"B\[(\d+)\]\s*&\s*(0[xX][0-9A-Fa-f]+|\d+)\b")
_RE_SHIFT = re.compile(r"B\[(\d+)\]\s*>>\s*(\d+)")
_RE_LINEAR = re.compile(r"^\(?\s*B\[(\d+)\]\s*(?:([*/])\s*(-?\d+(?:\.\d+)?))?\s*\)?\s*(?:([+-])\s*(\d+(?:\.\d+)?))?$")
_RE_ONLYBIT = re.compile(r"^\(?\s*B\[(\d+)\]\s*&\s*\(\s*1\s*<<\s*(\d+)\s*\)\s*\)?$")
_RE_RANGEONLY = re.compile(r"^B\[(\d+)~(\d+)\]$")


def _fmt(x: float) -> str:
    s = format(x, ".12g")
    if "e" in s:
        m, e = s.split("e")
        s = f"{m}e{e[0]}{int(e[1:]):02d}"
    return s


def analyze_formula(f: str) -> dict:
    """پورت خط‌به‌خط analyzeFormula در main.go نسخه ۱۵ (بدون حدس)."""
    out = {"byte_address": "", "bit_address": "", "coefficient": "", "offset": "", "formula_class": ""}
    f = (f or "").strip()
    if not f:
        return out
    idx = []
    for m in _RE_BYTE.finditer(f):
        k = m.group(1) + ("~" + m.group(2) if m.group(2) else "")
        if k not in idx:
            idx.append(k)
    out["byte_address"] = ",".join(idx)

    bits = [f"{m.group(1)}.{m.group(2)}" for m in _RE_BITFLAG.finditer(f)]
    for m in _RE_BITMASK.finditer(f):
        v = int(m.group(2), 16) if m.group(2).lower().startswith("0x") else int(m.group(2))
        if v > 0 and v & (v - 1) == 0:
            bits.append(f"{m.group(1)}.{v.bit_length() - 1}")
    bits += [f"{m.group(1)}>>{m.group(2)}" for m in _RE_SHIFT.finditer(f)]
    out["bit_address"] = ",".join(bits)

    if "PIDOF" in f:
        cls = "pid_reference"
    elif "?" in f:
        cls = "conditional"
    elif _RE_ONLYBIT.match(f):
        cls = "bit_flag"
    elif _RE_RANGEONLY.match(f):
        cls = "byte_range"
    elif "INFO{" in f or ("O[" in f and "C[" in f):
        cls = "dtc_decode"
    else:
        m = _RE_LINEAR.match(f)
        if m:
            cls, c = "linear_single_byte", 1.0
            if m.group(3):
                v = float(m.group(3))
                if m.group(2) == "/":
                    if v != 0:
                        c = 1 / v
                    else:
                        cls = "expression"
                else:
                    c = v
            o = 0.0
            if m.group(5):
                o = float(m.group(5)) * (-1 if m.group(4) == "-" else 1)
            if cls == "linear_single_byte":
                out["coefficient"], out["offset"] = _fmt(c), _fmt(o)
        elif len(idx) > 1:
            cls = "multi_byte"
        elif not idx:
            cls = "text_or_constant"
        else:
            cls = "expression"
    out["formula_class"] = cls
    return out


def bit_lsb_index(formula: str) -> str:
    """برای B[n]&(1<<k): سیکار بیت k را از سمت MSB می‌خواند؛ اندیس LSB = 7-k."""
    out = []
    for m in _RE_BITFLAG.finditer(formula or ""):
        k = int(m.group(2))
        if k <= 7:
            out.append(f"{m.group(1)}.{7 - k}")
    return ",".join(out)

# ---------------------------------------------------------------------------
# ۴) ارزیاب امن (معادل eval پایتون برای عبارات عددی)
# ---------------------------------------------------------------------------
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow, ast.LShift: operator.lshift, ast.RShift: operator.rshift,
        ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor}
_UN = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Invert: operator.invert, ast.Not: operator.not_}
_CMP = {ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Eq: operator.eq, ast.NotEq: operator.ne}


class UnsafeExpression(ValueError):
    pass


def safe_eval(expr: str):
    if not expr or not str(expr).strip():
        raise UnsafeExpression("empty expression")
    tree = ast.parse(str(expr), mode="eval")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 64:
                raise UnsafeExpression("exponent too large")
            if isinstance(n.op, ast.LShift) and b > 256:
                raise UnsafeExpression("shift too large")
            return _BIN[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UN:
            return _UN[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Compare) and all(type(o) in _CMP for o in n.ops):
            left = ev(n.left)
            for op, comp in zip(n.ops, n.comparators):
                right = ev(comp)
                if not _CMP[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(n, ast.BoolOp):
            vals = [ev(v) for v in n.values]
            if isinstance(n.op, ast.And):
                r = True
                for v in vals:
                    r = v
                    if not v:
                        return v
                return r
            for v in vals:
                if v:
                    return v
            return vals[-1]
        if isinstance(n, ast.IfExp):
            return ev(n.body) if ev(n.test) else ev(n.orelse)
        if isinstance(n, ast.Tuple):
            return tuple(ev(e) for e in n.elts)
        raise UnsafeExpression(f"unsupported node: {type(n).__name__}")

    return ev(tree)

# ---------------------------------------------------------------------------
# ۵) پورت وفادار توابع سیکار
# ---------------------------------------------------------------------------
_END_CHARS = set("1234567890[]()B<-&")


def _to_bytes(resp_hex: str) -> bytes:
    s = (resp_hex or "").replace(" ", "")
    if not s:
        return b""
    if not all(c in "0123456789abcdefABCDEF" for c in s):
        return b""
    if len(s) % 2:
        s = "0" + s
    return bytes.fromhex(s)


def _end_btag(i: int, s: str) -> int:
    """پورت customGetEndBTagIndex: اندیس اولین کاراکتری که در B[... انتها نمی‌آید."""
    i2 = i
    while i2 < len(s):
        if s[i2] not in _END_CHARS:
            return i2
        i2 += 1
    return len(s)


def _hex_to_binary(h: str) -> str:
    """پورت HexToBinary از app_Info.py."""
    if h == "":
        return ""
    b = bin(int(h, 16))[2:]
    if len(b) % 8:
        b = "0" * (8 - len(b) % 8) + b
    return b


@dataclass
class EvalTrace:
    expression: str = ""
    dropped_text: List[str] = field(default_factory=list)
    bit_ops: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _custom_value(sub: str, resp_hex: str, ctype: int, tr: EvalTrace) -> str:
    """پورت customGetValueExp از Run_Request.py."""
    data = _to_bytes(resp_hex)
    n = len(data)
    str4 = ""

    for part in sub.split("B"):
        if not part.startswith("["):
            continue
        start = part[1:]  # بعد از "["
        k = start.find("~")
        if k == -1:
            k = start.find("]")
            start_idx = start[0:k] if k != -1 else start[0:-1]
            end_idx = start_idx
        else:
            end_part = start[k:]
            start_idx = start[0:k]
            k2 = end_part.find("]")
            end_idx = end_part[0:k2] if k2 != -1 else end_part

        if start_idx == "":
            start_idx = "-1"

        try:
            si = int(start_idx)
        except ValueError:
            si = -1

        if n >= si and si >= 0:
            try:
                ei = int(end_idx)
            except ValueError:
                ei = si
            i = si - 1  # سیکار B[1] را به‌عنوان اولین بایت می‌خواند → اندیس 0 در آرایه
            if n <= i or n < ei:
                str4 += resp_hex.replace(" ", "")
            else:
                if i < 0:
                    i = n + i  # B[0] → آخرین بایت
                if 0 <= i < n:
                    str4 += "%02x" % data[i]
                if si == 0:
                    tr.notes.append("B[0] در سورس سیکار آخرین بایت را برمی‌گرداند")

    if ctype != 2:
        if ctype == 5:  # cmd_ClearTrouble - متن
            try:
                str4 = "".join(chr(int(str4[i:i + 2], 16)) for i in range(0, len(str4), 2))
            except (ValueError, IndexError):
                pass
        elif ctype == 6:  # cmd_ConnectToDevice_ASAN - الفبای خاص
            alpha = "0123456789ABCDEFGHJKLMNPRSTUWXYZ"
            try:
                str4 = "".join(alpha[int(str4[i:i + 2], 16)] for i in range(0, len(str4), 2) if int(str4[i:i + 2], 16) < len(alpha))
            except (ValueError, IndexError):
                pass
        return str4

    # ctype == 2: پردازش عملگر بیتی
    if "&" in sub:
        bit_value_part = sub[sub.find("&(") + 2:] if "&(" in sub else ""
        num_shift_part = sub[sub.find("<<") + 2:] if "<<" in sub else ""
        if bit_value_part:
            bit_value_part = bit_value_part[0:bit_value_part.find("<<")] if "<<" in bit_value_part else bit_value_part
        if num_shift_part:
            num_shift_part = num_shift_part[0:num_shift_part.find(")")] if ")" in num_shift_part else num_shift_part

        b = _hex_to_binary(str4)
        if len(b) > 7:
            try:
                width = bit_value_part.count("1") if bit_value_part else 1
                ns = int(num_shift_part) if num_shift_part else 0
                seg = b[ns:ns + width]
                tr.bit_ops.append(f"{sub}: {width} بیت از موقعیت {ns} سمت MSB رشته {len(b)} بیتی "
                                  f"(= بیت LSB شماره {len(b) - 1 - ns})")
                return str(int(seg, 2)) if seg else "0"
            except (ValueError, IndexError):
                return "0"
        raise ValueError("HexToBinary length <= 7 (رفتار سورس سیکار)")

    return str(int(str4, 16) if str4 else 0)


_BTOKEN = re.compile(r"B\[[^\]]*\]?")
_BITOP = re.compile(r"&\(\d+<<\d+\)")


def make_expression(formula: str, resp_hex: str, ctype: int, tr: Optional[EvalTrace] = None) -> str:
    """پورت make_expression از Run_Request.py. جایگزینی B[n] با مقدار از پاسخ."""
    tr = tr or EvalTrace()
    s = formula or ""
    max_iter = len(s.split("B")) + 2
    for _ in range(max_iter):
        s = s.replace(" ", "")
        i = s.find("B[")
        if i < 0:
            break
        sub = s[i:_end_btag(i, s)]
        # تشخیص متنی که بعد از جایگزینی حذف می‌شود
        rest = _BITOP.sub("", _BTOKEN.sub("", sub)) if ctype == 2 else _BTOKEN.sub("", sub)
        if rest.strip():
            tr.dropped_text.append(f"«{sub}» → «{rest}» حذف می‌شود (رفتار سورس)")
        try:
            replacement = _custom_value(sub, resp_hex, ctype, tr)
            s = s.replace(sub, replacement, 1)
        except Exception as e:
            tr.notes.append(f"خطا در جایگزینی {sub}: {e}")
            break
    tr.expression = s
    return s


def _calc_from_condition(s: str):
    """پورت calcFromCondition: تبدیل عبارت شرطی ? : به if/else پایتون."""
    new_cond, remaining = "", s
    while True:
        i = remaining.find("?")
        if i == -1:
            break
        _if = remaining[0:i]
        remaining = remaining[i + 1:]
        i = remaining.find(":")
        if i == -1:
            break
        _then = remaining[0:i]
        new_cond += f"{_then} if {_if} else "
        after = remaining[i + 1:]
        if after.find("?") == -1:
            new_cond += after
            break
        remaining = after
    if not new_cond:
        return safe_eval(s)
    return safe_eval(new_cond)


def calc_formula(expr: str, decimals: int):
    """customCalc_formula: در خطا استثنا بالا می‌رود (نه مقدار ساختگی)."""
    if not expr or not str(expr).strip():
        raise ValueError("empty expression")
    if "?" in str(expr).upper():
        return _calc_from_condition(str(expr))
    result = float(safe_eval(str(expr)))
    if decimals and decimals > 0:
        return format(result, f".{int(decimals)}f")
    return format(result, "g")


@dataclass
class EvalResult:
    value: Optional[str]
    status: str
    note: str = ""
    expression: str = ""
    bit_ops: str = ""


def evaluate(formula: str, resp_hex: str, ctype=2, decimals=0) -> EvalResult:
    """شبیه‌سازی دقیق مقدار response.value در سیکار."""
    f = (formula or "")
    ct = parse_ctype(ctype)
    ct = 2 if ct is None else ct

    if not f.strip():
        return EvalResult(None, "no_formula")

    cls = analyze_formula(f)["formula_class"]

    if ct in TROUBLE_CTYPES or cls == "dtc_decode":
        return EvalResult(None, "dtc_decode",
                          "فرمول دیکد DTC در مسیر خواندن خطا (نه make_expression) اجرا می‌شود")

    if "PIDOF" in f.upper():
        return EvalResult(None, "pid_reference",
                          "PIDOF به‌صورت پویا در زمان اجرا حل می‌شود؛ نیاز به وضعیت سایر PIDها دارد")

    if cls == "byte_range" and "~" in f:
        return EvalResult(None, "range_not_supported_by_reference_runtime",
                          "B[n~m] در مسیر make_expression سیکار پشتیبانی نمی‌شود (فقط در تحلیل ایستا)")

    tr = EvalTrace()
    try:
        expr = make_expression(f, resp_hex, ct, tr)
    except Exception as e:
        return EvalResult(None, "reference_runtime_error",
                          f"make_expression شکست خورد: {e}",
                          bit_ops=" | ".join(tr.bit_ops))

    if not expr.strip():
        return EvalResult(None, "reference_runtime_error",
                          "make_expression خروجی خالی داد",
                          expression=expr)

    # بررسی اینکه آیا فقط عدد ساده است یا نیاز به محاسبه دارد
    try:
        float(expr)
        is_plain_number = True
    except ValueError:
        is_plain_number = False

    if ct in EXPR_ONLY_CTYPES or is_plain_number:
        status = "ok"
        if tr.dropped_text:
            status = "reference_runtime_drops_text"
        return EvalResult(expr, status,
                          note=" | ".join(tr.notes) + ("; " if tr.notes else "") +
                               ("متن حذف‌شده: " + " | ".join(tr.dropped_text) if tr.dropped_text else ""),
                          expression=expr,
                          bit_ops=" | ".join(tr.bit_ops))

    if ct in CALC_CTYPES:
        try:
            val = calc_formula(expr, decimals)
            status = "ok"
            if tr.dropped_text:
                status = "reference_runtime_drops_text"
            return EvalResult(str(val), status,
                              note=" | ".join(tr.notes) + ("; " if tr.notes else "") +
                                   ("متن حذف‌شده: " + " | ".join(tr.dropped_text) if tr.dropped_text else ""),
                              expression=expr,
                              bit_ops=" | ".join(tr.bit_ops))
        except Exception as e:
            return EvalResult(None, "reference_runtime_error",
                              f"calc_formula شکست خورد: {e}; عبارت={expr}",
                              expression=expr,
                              bit_ops=" | ".join(tr.bit_ops))

    return EvalResult(None, "not_evaluated_ctype",
                      f"ctype={ct} ({CTYPE_NAMES.get(ct, 'نامشخص')}) در این مسیر محاسبه نمی‌شود",
                      expression=expr)
