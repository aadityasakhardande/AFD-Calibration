import telnetlib
import time
import socket
import traceback

HOST = "192.168.11.228"
PORT = 23

# Log files
FORCE_LOG = "force_log.txt"
SV_LOG = "sv_log.txt"
SO_LOG = "so_log.txt"
ERROR_LOG = "error_log.txt"


def log_error(msg):
    ts = time.time()
    with open(ERROR_LOG, "a") as f:
        f.write(f"{ts}, {msg}\n")


def telnet_connect():
    while True:
        try:
            print("Connecting to AFD310...")
            tn = telnetlib.Telnet(HOST, PORT, timeout=5)
            tn.read_until(b">>")
            print("Connected.")
            return tn
        except Exception as e:
            log_error(f"Connection error: {repr(e)}")
            time.sleep(1)


def read_line(tn):
    """Reads a full line safely."""
    return tn.read_until(b"\n").decode(errors="ignore").strip()


def poll_command(tn, command):
    """Send a command and return the single line result after echo."""
    tn.write(command.encode("ascii") + b"\n")

    # Discard echo
    _ = read_line(tn)

    # Read data line
    result = read_line(tn)

    # Read prompt
    tn.read_until(b">>")
    return result


def main():
    tn = telnet_connect()

    while True:
        try:
            ts = time.time()

            # ---- Read actualForce ----
            force = poll_command(tn, "actualForce")
            with open(FORCE_LOG, "a") as f:
                f.write(f"{ts}, {force}\n")
            print(f"Force: {force}")

            # ---- Read sv ----
            sv = poll_command(tn, "sv")
            with open(SV_LOG, "a") as f:
                f.write(f"{ts}, {sv}\n")

            # ---- Read so ----
            so = poll_command(tn, "so")
            with open(SO_LOG, "a") as f:
                f.write(f"{ts}, {so}\n")

            time.sleep(1)

        except (EOFError, ConnectionResetError, socket.timeout) as e:
            log_error(f"Connection lost: {repr(e)}")
            print("Connection lost, reconnecting...")
            tn = telnet_connect()

        except Exception as e:
            log_error(f"Unexpected error: {repr(e)} | Trace: {traceback.format_exc()}")
            print("Unexpected error. Logged.")
            time.sleep(1)


if __name__ == "__main__":
    main()
