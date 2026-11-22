"""노드 main 공용: Ctrl-C·pkill(SIGTERM) 에 트레이스백 없이 끝낸다.

rclpy 기본 신호 처리기는 신호가 오면 문맥을 바로 닫아서, 그 순간 돌던 타이머 콜백의 publish 나 다음 대기 집합 생성이
`RCLError: ... context is not valid` 로 터진다(ExternalShutdownException 만 잡아서는 못 막음).
신호 처리기에서 KeyboardInterrupt 를 던지는 방식은 가끔 끝나지 않고 멈췄다. 그래서 rclpy 신호 처리를 끄고,
돌기 시작한 뒤의 신호는 깃발만 세운다. spin_once 를 짧게 돌다가 깃발을 보면 빠져나와, 노드를 지운 뒤 우리가 문맥을 닫는다.

노드를 만드는 동안(예: sbs_player 가 PNG 를 읽는 수 초)에는 깃발 처리기를 아직 달지 않는다 — 달아 두면 신호가 깃발만
세우고 로딩이 끝날 때까지 안 끝나서 launch 가 SIGINT 5 초 뒤 SIGTERM, 다시 SIGKILL 로 넘어간다.
그 구간의 SIGINT·SIGTERM 은 KeyboardInterrupt 로 받아 조용히 끝낸다.
"""
from __future__ import annotations

import signal
from typing import Callable

import rclpy
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions


def spin_quietly(make_node: Callable[[], Node], poll_s: float = 0.1) -> None:
    stop = []
    for sig in (signal.SIGINT, signal.SIGTERM):                    # 로딩 중: 신호 → KeyboardInterrupt
        signal.signal(sig, signal.default_int_handler)
    node = None
    executor = None
    try:
        rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
        node = make_node()
        for sig in (signal.SIGINT, signal.SIGTERM):                # 돌기 시작: 신호 → 깃발
            signal.signal(sig, lambda signum, frame: stop.append(signum))
        executor = SingleThreadedExecutor()
        executor.add_node(node)
        while not stop and rclpy.ok():
            executor.spin_once(timeout_sec=poll_s)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):                # 정리 중 신호는 무시(정리를 끊지 않게)
            signal.signal(sig, signal.SIG_IGN)
        if executor is not None:
            executor.shutdown()
        if node is not None:
            node.destroy_node()
        rclpy.try_shutdown()
