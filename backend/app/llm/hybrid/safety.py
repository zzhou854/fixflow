"""Auditable dual-channel safety detection with negation/hypothetical guards."""

from __future__ import annotations

import re

from app.agent.enums import SafetyFlag
from app.llm.hybrid.models import NormalizedResidentFactsV1, SafetySignal

_NEGATION = re.compile(r"(没有|没闻到|未发现|并无|不是|不存在|排除).{0,5}$")
_HYPOTHETICAL = re.compile(r"(如果|假如|万一|以后).{0,10}$")
_INLINE_NEGATION = re.compile(
    r"(没有|并无|未|没).{0,6}(异味|味道|臭味|泄漏|漏气|冒烟|浓烟|烟雾|"
    r"明火|起火|着火|火花|触电|漏电|带电|积水|被困|受伤)"
)

_SIGNAL_PATTERNS: dict[SafetySignal, tuple[re.Pattern[str], ...]] = {
    SafetySignal.GAS_ODOR: (
        re.compile(r"(燃气|煤气).{0,4}(异味|味道|臭味)"),
        re.compile(r"(燃气味|煤气味)"),
    ),
    SafetySignal.GAS_LEAK: (re.compile(r"(燃气|煤气).{0,5}(泄漏|漏气)"),),
    SafetySignal.SMOKE: (re.compile(r"(冒烟|浓烟|烟雾)"),),
    SafetySignal.OPEN_FLAME: (re.compile(r"(明火|起火|着火|火势)"),),
    SafetySignal.ELECTRICAL_ARC: (
        re.compile(r"(电线|插座|配电|电器|电表箱|开关).{0,8}(打火|火花|冒烟|起火)"),
        re.compile(r"冒烟风险"),
    ),
    SafetySignal.ELECTRIC_SHOCK: (re.compile(r"(触电|漏电|带电)"),),
    SafetySignal.ACTIVE_FLOODING: (
        re.compile(r"(大量|不停|正在|爆裂|成股).{0,8}(漏水|淌水|涌出|积水|往下流)"),
        re.compile(
            r"(水管爆|水管.{0,4}爆开|大量水|水正在涌|不断往下涌水|"
            r"积水(越来越多|迅速增多))"
        ),
    ),
    SafetySignal.WATER_NEAR_ELECTRICITY: (
        re.compile(
            r"(水|积水|漏水|淌水|水滴).{0,16}"
            r"(电线|插座|插排|插线板|电器|冰箱|灯|浴霸)"
        ),
        re.compile(
            r"(电线|插座|插排|插线板|电器|灯|浴霸).{0,16}"
            r"(水|积水|漏水|淌水|水滴)"
        ),
    ),
    SafetySignal.ELEVATOR_ENTRAPMENT: (
        re.compile(r"(困|被困).{0,5}电梯"),
        re.compile(r"电梯.{0,8}(困|门打不开|下坠|坠落)"),
    ),
    SafetySignal.FALL_HAZARD: (
        re.compile(r"(坠落|下坠|马上会掉|可能.{0,3}掉落)"),
        re.compile(r"(玻璃|天花板|吊顶).{0,8}松动"),
    ),
    SafetySignal.PERSON_INJURED: (
        re.compile(r"(有人|人员|住户|孩子|家人|老人).{0,8}(受伤|流血)"),
        re.compile(r"(砸伤|摔伤|划伤).{0,5}(人|家人|住户)"),
        re.compile(r"(砸到|撞到).{0,3}(人|家人|住户)"),
    ),
    SafetySignal.PERSON_TRAPPED: (
        re.compile(r"(孩子|小孩|人员|住户|儿童|老人|家人).{0,10}(被困|反锁|困在|锁在)"),
        re.compile(
            r"(被困|反锁|困在|锁在).{0,10}"
            r"(阳台|卫生间|房间|卧室|次卧|储物间|电梯)"
        ),
    ),
}


def _active_match(text: str, pattern: re.Pattern[str]) -> bool:
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 12) : match.start()]
        if (
            _NEGATION.search(prefix)
            or _HYPOTHETICAL.search(prefix)
            or _INLINE_NEGATION.search(match.group(0))
        ):
            continue
        return True
    return False


def detect_safety_signals(facts: NormalizedResidentFactsV1) -> tuple[SafetySignal, ...]:
    signals = {
        item.signal
        for item in facts.safety_evidence
        if any(
            _active_match(facts.source_text, pattern) for pattern in _SIGNAL_PATTERNS[item.signal]
        )
    }
    for signal, patterns in _SIGNAL_PATTERNS.items():
        if any(_active_match(facts.source_text, pattern) for pattern in patterns):
            signals.add(signal)
    return tuple(sorted(signals, key=lambda item: item.value))


def map_safety_flags(signals: tuple[SafetySignal, ...]) -> tuple[SafetyFlag, ...]:
    flags: set[SafetyFlag] = set()
    signal_set = set(signals)
    if signal_set:
        flags.add(SafetyFlag.IMMEDIATE_DANGER)
    if (
        SafetySignal.ACTIVE_FLOODING in signal_set
        or SafetySignal.WATER_NEAR_ELECTRICITY in signal_set
    ):
        flags.add(SafetyFlag.ACTIVE_FLOODING)
    if signal_set.intersection(
        {
            SafetySignal.ELECTRICAL_ARC,
            SafetySignal.ELECTRIC_SHOCK,
            SafetySignal.WATER_NEAR_ELECTRICITY,
        }
    ):
        flags.add(SafetyFlag.ELECTRICAL_HAZARD)
    if (
        SafetySignal.PERSON_TRAPPED in signal_set
        and SafetySignal.ELEVATOR_ENTRAPMENT not in signal_set
    ):
        flags.add(SafetyFlag.LOCKOUT_RISK)
    general_review = signal_set.intersection(
        {
            SafetySignal.GAS_ODOR,
            SafetySignal.GAS_LEAK,
            SafetySignal.SMOKE,
            SafetySignal.OPEN_FLAME,
            SafetySignal.ELEVATOR_ENTRAPMENT,
            SafetySignal.FALL_HAZARD,
            SafetySignal.PERSON_INJURED,
        }
    )
    if general_review and not signal_set.intersection(
        {SafetySignal.ELECTRICAL_ARC, SafetySignal.ELECTRIC_SHOCK}
    ):
        flags.add(SafetyFlag.OTHER_REVIEW_REQUIRED)
    return tuple(sorted(flags, key=lambda item: item.value))
