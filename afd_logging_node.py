import time
import socket
import threading
from collections import deque
from datetime import datetime

import matplotlib
matplotlib.use('Agg')          # headless-safe; swap to 'TkAgg' if you want live display
import matplotlib.pyplot as plt

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor

from std_msgs.msg import Empty
from system_state_tracker_msgs.msg import SystemState

from functions import (
    SandingTracker, pct_force, rest_force,
    telnet_connect, check_force_error, poll_command
)

# ── SS State values (uint8) — from system_state_tracker.cpp / test_state_update.cpp ──
# HEALTH_PENDING = 7 is confirmed numerically in test_state_update.cpp.
# Use the message constants if your msg definition exposes them; otherwise use these names.
class SState:
    COMPLEX_WORKING = "COMPLEX_WORKING"   # SS button pressed → scan phase starts
    JOB_RUNNING     = "JOB_RUNNING"       # sand job executing
    JOB_DONE        = "JOB_DONE"          # completed successfully
    JOB_FAIL        = "JOB_FAIL"          # job failed
    STOPPING        = "STOPPING"          # stop button pressed

# States that mean "SS is actively running" → START your script
SS_ACTIVE_STATES   = {SystemState.COMPLEX_WORKING, SystemState.JOB_RUNNING}

# States that mean "SS has ended" → STOP your script
SS_INACTIVE_STATES = {SystemState.JOB_DONE, SystemState.JOB_FAIL, SystemState.STOPPING}

# ── Constants (from your original script) ────────────────────────────────────
PORT       = 23
WINDOW     = 10.0
MAX_POINTS = 500


class AFDLoggerNode(Node):

    def __init__(self):
        super().__init__('afd_logger_node')
        self.get_logger().info('AFDLoggerNode initialising...')

        # ── State ─────────────────────────────────────────────────────────────
        self._ss_active   = False          # True while SS is running
        self._stop_event  = threading.Event()   # signals the poll thread to stop
        self._poll_thread = None

        # ── Tracker / log paths (from your script) ────────────────────────────
        self._tracker      = SandingTracker()
        self._position_log = self._tracker.position_log()
        self._pct_log      = self._tracker.pct_log()

        # ── ROS 2 Subscriptions ───────────────────────────────────────────────
        # Primary: watch system state (published every 100 ms by SystemStateTracker)
        self.create_subscription(
            SystemState,
            '/system_state_tracker/system_state',
            self._on_system_state,
            10
        )

        # Secondary / redundant: job_done fires on ANY job end (stop, complete, fail)
        # Published by SystemStateTracker::jobDone() in system_state_tracker.cpp
        self.create_subscription(
            Empty,
            '/ui_manager/job_done',
            self._on_job_done,
            10
        )

        self.get_logger().info('AFDLoggerNode ready — waiting for SS to start.')

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_system_state(self, msg: SystemState):
        """
        Called every ~100 ms.  msg.state is a uint8 matching the SState enum.
        Confirmed field name: .state  (from robot_health_monitor.cpp: msg->state)
        """
        state = msg.state

        if state in SS_ACTIVE_STATES and not self._ss_active:
            self.get_logger().info(f'SS started (state={state}). Starting AFD logger...')
            self._start_logging()

        elif state in SS_INACTIVE_STATES and self._ss_active:
            self.get_logger().info(f'SS ended via state change (state={state}). Stopping AFD logger...')
            self._stop_logging()

    def _on_job_done(self, _msg: Empty):
        """
        Redundant stop signal — /ui_manager/job_done is published by
        SystemStateTracker::jobDone() on stop, complete, fail, and reset.
        """
        if self._ss_active:
            self.get_logger().info('Received /ui_manager/job_done. Stopping AFD logger...')
            self._stop_logging()

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def _start_logging(self):
        self._ss_active = True
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name='afd_poll_thread'
        )
        self._poll_thread.start()

    def _stop_logging(self):
        self._ss_active = False
        self._stop_event.set()          # signals the loop to exit cleanly
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=3.0)
        self.get_logger().info('AFD logger stopped.')

    # ── Core polling loop (your original script logic) ────────────────────────

    def _poll_loop(self):
        """
        Runs in a background thread while SS is active.
        Mirrors your original main() loop exactly, with _stop_event as the exit signal.
        """
        self.get_logger().info('Poll loop starting — connecting to AFD...')

        try:
            tn = telnet_connect()
        except Exception as e:
            self.get_logger().error(f'Failed to connect to AFD: {e}')
            self._ss_active = False
            return

        times     = deque(maxlen=MAX_POINTS)
        positions = deque(maxlen=MAX_POINTS)

        fig, ax = plt.subplots()
        line, = ax.plot([], [], label='actualPosition')
        ax.set_xlabel('Time (s)')
        ax.set_ylabel('Position')
        ax.legend()
        ax.set_ylim(0, 20)
        ax.set_xlim(0, WINDOW)

        start_time = time.time()

        while not self._stop_event.is_set():
            try:
                ts  = datetime.now().strftime('%H:%M:%S')
                t   = time.time() - start_time

                force        = poll_command(tn, 'actualForce')
                pos          = poll_command(tn, 'actualPosition')
                dial_pos     =  round(pos - 10, 4)
                cmd_position = poll_command(tn, 'commandPosition')
                cmd_force    = poll_command(tn, 'commandForce')
                pro_error    = poll_command(tn, 'proportionalError')

                # ── Log to file ───────────────────────────────────────────────
                with open(self._position_log, 'a') as f:
                    f.write(f'{ts}, {dial_pos}, {cmd_position}, {force}, {cmd_force}, {pro_error}\n')

                # ── PCT Motion Tracking ───────────────────────────────────────────

                # Start logging when PCT starts (cmd_force reached)
                if not self._pct_logging_started and force == pct_force:
                    self._pct_logging_started = True
                    self._pct_logging_stop_time = None
                    self.get_logger().info("PCT logging started.")

                # Schedule stop 5 sec after reaching rest_force
                if self._pct_logging_started:
                    if force == self._rest_force and self._pct_logging_stop_time is None:
                        self._pct_logging_stop_time = time.time() + 5.0
                        self.get_logger().info("PCT stop timer triggered (5s).")

                # Write PCT log while active
                if self._pct_logging_started:
                    with open(self._pct_log, "a") as f:
                        f.write(f"{ts}, {dial_pos}, {cmd_position}, {force}, {cmd_force}, {pro_error}\n")

                # Stop after 5 sec window
                if self._pct_logging_stop_time is not None and time.time() >= self._pct_logging_stop_time:
                    self._pct_logging_started = False
                    self._pct_logging_stop_time = None
                    self.get_logger().info("PCT logging stopped.")

                # ── Sanding tracker ───────────────────────────────────────────
                self._tracker.process_new_entry(ts, cmd_force)

                # ── Console output ────────────────────────────────────────────
                self.get_logger().info(
                    f'Pos: {dial_pos}  Force: {force}  CmdForce: {cmd_force}  ProErr: {pro_error}'
                )

                # ── Live plot update ──────────────────────────────────────────
                times.append(t)
                positions.append(pos)
                line.set_data(times, positions)
                ax.set_xlim(max(0, t - WINDOW), t)   # ✅ uses WINDOW constant
                plt.pause(0.001)

                time.sleep(0.05)   # 20 Hz

            except (EOFError, ConnectionResetError, socket.timeout):
                self.get_logger().warn('AFD connection lost. Reconnecting...')
                try:
                    tn = telnet_connect()
                except Exception as e:
                    self.get_logger().error(f'Reconnect failed: {e}')
                    break

            except Exception as e:
                self.get_logger().error(f'Unexpected error in poll loop: {e}')
                break

        plt.close(fig)
        self.get_logger().info('Poll loop exited.')


# ── Entry point ───────────────────────────────────────────────────────────────

def main(args=None):
    rclpy.init(args=args)
    node = AFDLoggerNode()

    # MultiThreadedExecutor so ROS callbacks don't block the poll thread
    executor = MultiThreadedExecutor()
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node._stop_logging()   # clean shutdown
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()