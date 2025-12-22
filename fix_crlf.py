import os

files = [
    "deploy/alarm.service",
    "deploy/check_status.sh",
    "deploy/debug_run.sh",
    "deploy/enable_autostart.sh",
    "deploy/install.sh",
    "deploy/sensor.service",
    "deploy/watch_logs.sh"
]

for file_path in files:
    full_path = os.path.join(os.getcwd(), file_path)
    if os.path.exists(full_path):
        with open(full_path, 'rb') as f:
            content = f.read()
        if b'\r\n' in content:
            content = content.replace(b'\r\n', b'\n')
            with open(full_path, 'wb') as f:
                f.write(content)
            print(f"Converted {file_path} to LF")
        else:
            print(f"{file_path} already LF")
    else:
        print(f"File not found: {full_path}")

