# -*- coding: utf-8 -*-
"""سازگاری با API قدیمی: AdvancedFormulaParser.parse(formula, raw_bytes).
محاسبه به موتور پورت‌شده سیکار سپرده می‌شود؛ اندیس B[n] مانند سیکار ۱-پایه است.
در صورت خطا None برمی‌گرداند (نه مقدار ساختگی)."""
from typing import List, Optional

from modules.sicar_formula import evaluate


class AdvancedFormulaParser:
    @staticmethod
    def parse(formula: str, raw_bytes: List[int], ctype: int = 2, decimals: int = 2) -> Optional[float]:
        resp = " ".join("%02X" % (b & 0xFF) for b in raw_bytes)
        r = evaluate(formula, resp, ctype, decimals)
        if r.status in ("ok", "reference_runtime_drops_text") and r.value is not None:
            try:
                return float(r.value)
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def evaluate(formula: str, raw_bytes: List[int], ctype: int = 2, decimals: int = 2):
        """نتیجه کامل (value, status, note, expression)."""
        return evaluate(formula, " ".join("%02X" % (b & 0xFF) for b in raw_bytes), ctype, decimals)
