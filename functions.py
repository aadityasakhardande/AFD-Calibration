import os
import telnetlib
import time
import yaml
import json
from pathlib import Path

HOST = "192.168.11.228"
LOG_FILE = "position_log.txt"
TIMELINE_FILE = f"BWP_SS/timeline{time.time()}.txt"
ERROR_LOG = "error_log.txt"
PORT = 23
WINDOW = 10.0

scan_force_file = "/home/gmr/dev-docker/ss/src/settings/launch/launch_arguments.yaml"               # To access Scan Force ("scan_force")
with open(scan_force_file, "r") as file:
    config = yaml.safe_load(file)
scan_force = float(config["scanning"]["scan_force"])

rest_force_file = "/home/gmr/dev-docker/ss/src/settings/launch/launch_arguments.yaml"                 # To access Rest Force ("static_force")   
with open(rest_force_file, "r") as file:
    config = yaml.safe_load(file)
rest_force = float(config["force"]["static_force"]) 

noncontact_force_file = "/home/gmr/dev-docker/ss/src/settings/launch/launch_arguments.yaml"         # To access Non-Contact Force (P2P)
with open(noncontact_force_file, "r") as file:
    config = yaml.safe_load(file)
noncontact_force = float(config["motion"]["noncontact_force"]) 

pct_force_file = "/home/gmr/dev-docker/runtime_data/profile_handler/run"                            # To access PCT Force ("force")
folders = [f for f in Path(pct_force_file).iterdir() if f.is_dir()]
latest_folder = max(folders, key=lambda f: f.stat().st_ctime)
json_file = None

for root, dirs, files in os.walk(latest_folder):
    if 'passTypeDetail.json' in files:
        json_file = os.path.join(root, 'passTypeDetail.json')
        break

if not json_file:
    raise FileNotFoundError(f"'passTypeDetail.json' not found in {latest_folder} or its subdirectories.")
with open(json_file, 'r') as file:
    data = json.load(file)

pct_force = float(data.get('pass_details', [{}])[0].get('force'))

print("Scan Force:", scan_force, type(scan_force))
print("Rest Force:", rest_force, type(rest_force))
print("Non-Contact Force:", noncontact_force, type(noncontact_force))
print("PCT Force:", pct_force, type(pct_force))

class SandingTracker:

    def __init__(self, output_file=TIMELINE_FILE):
        self.intervals = []
        self.current_force = None
        self.start_time = None
        self.output_file = output_file
        self.scanning_detected = False

    def get_state(self, force):
        if force == scan_force:
            return "Scanning"
        elif force == pct_force:
            return "PCT Motion"
        elif force == rest_force:
            return "Rest"
        elif force == noncontact_force:
            return "P2P Motion"
        else:
            return "Unknown"

    def process_new_entry(self, timestamp, cmd_force):
        try:
            cmd_force = float(cmd_force)
        except:
            return

        # First entry
        if self.current_force is None:
            self.current_force = cmd_force
            self.start_time = timestamp
            self.update_outputs(timestamp)

        # If force changed -> finalize old interval
        if cmd_force != self.current_force:

            self.intervals.append((
                self.current_force,
                self.start_time,
                timestamp,
                # self.get_state(self.current_force)          # Add State
            ))

            self.current_force = cmd_force
            self.start_time = timestamp

        # Always update outputs
        self.update_outputs(timestamp)

    def parse_time(self, ts):
        from datetime import datetime
        return datetime.strptime(ts, "%H:%M:%S")

    def build_table_string(self, current_time):
        lines = []
        lines.append("\nTIMELINE\n")
        lines.append(f"{'Cmd Force':<12}{'State':<15}{'Start Time':<15}{'End Time':<15}{'Duration (s)':<15}")
        lines.append("-" * 80)

        # Completed intervals
        for status, start, end, state in self.intervals:
            try:
                t1 = self.parse_time(start)
                t2 = self.parse_time(end)
                duration = (t2 - t1).total_seconds()
            except:
                duration = "N/A"
            lines.append(f"{status:<12}{state:<12}{start:<20}{end:<20}{duration}")

        # Current active interval
        if self.current_force is not None:
            try:
                t1 = self.parse_time(self.start_time)
                t2 = self.parse_time(current_time)
                live_duration = (t2 - t1).total_seconds()
            except:
                live_duration = "-"
            lines.append(f"{self.current_force:<12}{self.get_state(self.current_force):<12}{self.start_time:<20}{'-':<20}{live_duration}")

        return "\n".join(lines)

    def update_outputs(self, current_time):
        table = self.build_table_string(current_time)
        # ---- WRITE TO FILE (LIVE UPDATE) ----
        with open(self.output_file, "w") as f:
            f.write(table)
            
def telnet_connect():

    while True:
        try:
            print("Connecting to AFD310...")
            tn = telnetlib.Telnet(HOST, PORT, timeout=5)
            tn.read_until(b">>")
            print("Connected.")
            return tn
        except Exception:
            time.sleep(1)

def read_line(tn):

    return tn.read_until(b"\n").decode(errors="ignore").strip()

def poll_command(tn, command):

    tn.write(command.encode("ascii") + b"\n")
    _ = read_line(tn)        # echo
    result = read_line(tn)   # data
    tn.read_until(b">>")     # prompt
    return float(result)

def check_force_error(timestamp, actualForce, commandForce, proportional_error):

    THRESHOLD_FORCE = 0.5
    THRESHOLD_ERROR = 0.005

    if (abs(actualForce - commandForce) != THRESHOLD_FORCE) and (proportional_error > THRESHOLD_ERROR):
        with open(ERROR_LOG, "a") as f:
            f.write(f"POTENTIAL ERROR: {timestamp}, CommandForce={commandForce}, ProportionalError={proportional_error}\n")