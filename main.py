import os
import re
import time
import socket
import matplotlib.pyplot as plt
from datetime import datetime
from collections import deque
from functions import SandingTracker, telnet_connect, check_force_error, poll_command, file_number


POSITION_LOG = file_number()
HOST = "192.168.11.228"
ERROR_LOG = "error_log.txt"
PORT = 23
WINDOW = 10.0
MAX_POINTS = 500
tracker = SandingTracker()

def main():

    tn = telnet_connect()

    times = deque(maxlen=MAX_POINTS)
    positions = deque(maxlen=MAX_POINTS)

    # plt.ion()
    fig, ax = plt.subplots()
    line, = ax.plot([], [], label="actualPosition")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Position")
    ax.legend()
    ax.set_ylim(0, 20)
    ax.set_xlim(0, 10)   # initial window

    start_time = time.time()

    while True:

        try:
            ts = datetime.now().strftime("%H:%M:%S")
            t = time.time() - start_time
            
            force = poll_command(tn, "actualForce")                    # Read Force
            pos = poll_command(tn, "actualPosition")                   # Read Position
            dial_pos = round(pos - 10, 4) 
            cmd_position = poll_command(tn, "commandedPosition")         # Read Commanded Position
            cmd_force = poll_command(tn, "commandForce")               # Read Command Force
            pro_error = poll_command(tn, "proportionalError")          # Read Proportional Error


            # check_force_error(ts, force, cmd_force, pro_error)         # Flag Potential Errors

            with open(POSITION_LOG, "a") as f:
                f.write(f"{ts}, {dial_pos}, {cmd_position}, {force}, {cmd_force}, {pro_error}\n")

            tracker.process_new_entry(ts, cmd_force)

            print(f"Position: {dial_pos}, Commanded Position: {cmd_position}, Force: {force},  Command Force: {cmd_force}, Proportional Error: {pro_error}, ")

            times.append(t)
            positions.append(pos)
            line.set_data(times, positions)
            ax.set_xlim(max(0, t - 10), t)
            plt.pause(0.001)   
            time.sleep(0.05)   # 20 Hz

        ################################################## ERROR/CONNECTION LOST ##################################################
        except (EOFError, ConnectionResetError, socket.timeout):

            print("Connection lost. Reconnecting...")
            tn = telnet_connect()

        except Exception as e:

            print("Error:", e)
            time.sleep(0.1)
        
if __name__ == "__main__":
    main()



