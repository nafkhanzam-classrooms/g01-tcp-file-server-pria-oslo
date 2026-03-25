import socket
import json
import os
import struct
import threading

HOST = '127.0.0.1'
PORT = 5000
CLIENT_FILES_DIR = 'client_files'
BUFFER_SIZE = 4096


def ensure_client_dir():
    if not os.path.exists(CLIENT_FILES_DIR):
        os.makedirs(CLIENT_FILES_DIR)


def send_json(sock, data):
    payload = json.dumps(data).encode('utf-8')
    header = struct.pack('>I', len(payload))
    sock.sendall(header + payload)


def recv_json(sock):
    header = sock.recv(4)
    if not header:
        return None

    length = struct.unpack('>I', header)[0]

    payload = b''
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            return None
        payload += chunk

    return json.loads(payload.decode('utf-8'))


def send_file_bytes(sock, filepath):
    with open(filepath, 'rb') as f:
        while True:
            chunk = f.read(BUFFER_SIZE)
            if not chunk:
                break
            sock.sendall(chunk)


def recv_file_bytes(sock, filepath, filesize):
    received = 0

    with open(filepath, 'wb') as f:
        while received < filesize:
            remaining = filesize - received
            chunk = sock.recv(min(BUFFER_SIZE, remaining))

            if not chunk:
                return False

            f.write(chunk)
            received += len(chunk)

    return True


def handle_server_messages(sock):
    while True:
        try:
            message = recv_json(sock)

            if not message:
                print("\n[INFO] Koneksi ke server terputus.")
                break

            msg_type = message.get("type")

            if msg_type == "chat":
                print(f"\n[{message['sender']}] {message['message']}")

            elif msg_type == "info":
                print(f"\n[INFO] {message['message']}")

            elif msg_type == "error":
                print(f"\n[ERROR] {message['message']}")

            elif msg_type == "list_result":
                files = message.get("files", [])
                print("\n[SERVER FILES]")

                if not files:
                    print("Tidak ada file di server.")
                else:
                    for file in files:
                        print(f"- {file}")

            elif msg_type == "upload_ready":
                filename = message["filename"]
                filepath = os.path.join(CLIENT_FILES_DIR, filename)
                send_file_bytes(sock, filepath)

            elif msg_type == "upload_done":
                print(f"\n[INFO] {message['message']} : {message['filename']}")

            elif msg_type == "download_ready":
                filename = message["filename"]
                filesize = message["filesize"]
                filepath = os.path.join(CLIENT_FILES_DIR, filename)

                success = recv_file_bytes(sock, filepath, filesize)

                if success:
                    print(f"\n[INFO] Download berhasil: {filename}")
                else:
                    print(f"\n[ERROR] Download gagal: {filename}")

            else:
                print(f"\n[INFO] Pesan tidak diketahui: {message}")

        except Exception as e:
            print(f"\n[ERROR] Thread berhenti: {e}")
            break


def input_loop(sock, username):
    while True:
        try:
            text = input(f"{username}> ").strip()

            if not text:
                continue

            if text == "/list":
                send_json(sock, {"type": "command", "command": "list"})

            elif text.startswith("/upload"):
                parts = text.split(" ", 1)

                if len(parts) < 2 or not parts[1].strip():
                    print("[ERROR] Usage: /upload <filename>")
                    continue

                filename = parts[1].strip()
                filepath = os.path.join(CLIENT_FILES_DIR, filename)

                if not os.path.exists(filepath):
                    print(f"[ERROR] File '{filename}' tidak ada di folder client_files/")
                    continue

                filesize = os.path.getsize(filepath)

                send_json(sock, {"type": "command", "command": "upload", "filename": filename, "filesize": filesize})

            elif text.startswith("/download"):
                parts = text.split(" ", 1)

                if len(parts) < 2 or not parts[1].strip():
                    print("[ERROR] Usage: /download <filename>")
                    continue

                filename = parts[1].strip()

                send_json(sock, {"type": "command", "command": "download", "filename": filename})

            else:
                send_json(sock, {"type": "chat", "message": text})

        except Exception as e:
            print(f"[ERROR] Input loop stopped: {e}")
            break


def main():
    ensure_client_dir()

    username = input("Masukkan username: ").strip()
    if not username:
        username = "Anonymous"

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))

    send_json(sock, {"type": "join", "username": username})

    receiver_thread = threading.Thread(
        target=handle_server_messages,
        args=(sock,),
        daemon=True
    )
    receiver_thread.start()

    input_loop(sock, username)

    sock.close()


if __name__ == '__main__':
    main()