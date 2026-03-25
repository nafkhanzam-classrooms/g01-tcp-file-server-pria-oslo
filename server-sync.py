import socket
import json
import os
import struct

HOST = '127.0.0.1'
PORT = 5000
SERVER_FILES_DIR = 'server_files'
BUFFER_SIZE = 4096

clients = {}


def ensure_server_dir():
    if not os.path.exists(SERVER_FILES_DIR):
        os.makedirs(SERVER_FILES_DIR)


def send_json(sock, data):
    try:
        payload = json.dumps(data).encode('utf-8')
        header = struct.pack('>I', len(payload))
        sock.sendall(header + payload)
    except:
        pass


def recv_json(sock):
    try:
        header = sock.recv(4)
        length = struct.unpack('>I', header)[0]

        payload = b''
        while len(payload) < length:
            chunk = sock.recv(min(length - len(payload), BUFFER_SIZE))
            if not chunk:
                return None
            payload += chunk

        return json.loads(payload.decode('utf-8'))
    except:
        return None


def send_file_bytes(sock, filepath):
    try:
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(BUFFER_SIZE)
                if not chunk:
                    break
                sock.sendall(chunk)
    except:
        print("[ERROR] Gagal mengirim file.")


def recv_file_bytes(sock, filepath, filesize):
    received = 0

    try:
        with open(filepath, 'wb') as f:
            while received < filesize:
                remaining = filesize - received
                chunk = sock.recv(min(BUFFER_SIZE, remaining))

                if not chunk:
                    return False

                f.write(chunk)
                received += len(chunk)

        return True

    except:
        return False


def handle_command(client_sock, message):
    client = clients.get(client_sock)

    if not client:
        return

    command = message.get("command")

    if command == "list":
        try:
            files = sorted(os.listdir(SERVER_FILES_DIR))
            send_json(client_sock, {"type": "list_result", "files": files})
        except Exception as e:
            send_json(client_sock, {"type": "error", "message": str(e)})

    elif command == "upload":
        filename = os.path.basename(message.get("filename", "unknown"))
        filesize = message.get("filesize", 0)
        filepath = os.path.join(SERVER_FILES_DIR, filename)

        send_json(client_sock, {"type": "upload_ready", "filename": filename})

        if recv_file_bytes(client_sock, filepath, filesize):
            print(f"[UPLOAD-DONE] {client['username']} -> {filename}")
            send_json(client_sock, {
                "type": "upload_done",
                "filename": filename,
                "message": "Berhasil"
            })
        else:
            print(f"[UPLOAD-FAILED] {filename}")
            if os.path.exists(filepath):
                os.remove(filepath)

    elif command == "download":
        filename = os.path.basename(message.get("filename", ""))
        filepath = os.path.join(SERVER_FILES_DIR, filename)

        if os.path.exists(filepath):
            send_json(client_sock, {
                "type": "download_ready",
                "filename": filename,
                "filesize": os.path.getsize(filepath)
            })

            send_file_bytes(client_sock, filepath)

            print(f"[DOWNLOAD-DONE] {client['username']} <- {filename}")
        else:
            send_json(client_sock, {"type": "error", "message": "File tidak ada"})


def start_server():
    ensure_server_dir()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(1)

    print(f"[STARTED] Sync server running on {HOST}:{PORT}")

    try:
        while True:
            client_sock, addr = server_sock.accept()
            clients[client_sock] = {
                "username": None,
                "address": addr
            }

            print(f"[CONNECT] New client from {addr}")

            try:
                while True:
                    message = recv_json(client_sock)

                    if not message:
                        break

                    if clients[client_sock]["username"] is None:
                        clients[client_sock]["username"] = message.get("username", "User")

                        print(f"[LOGIN] {clients[client_sock]['username']}")

                        send_json(client_sock, {
                            "type": "info",
                            "message": "Welcome"
                        })
                        continue

                    if message.get("type") == "command":
                        handle_command(client_sock, message)

                    elif message.get("type") == "chat":
                        print(
                            f"[CHAT] {clients[client_sock]['username']}: "
                            f"{message.get('message')}"
                        )

            except ConnectionResetError:
                print(f"[DISCONNECT] Client {addr} memaksa tutup koneksi.")

            except Exception as e:
                print(f"[ERROR] {e}")

            finally:
                client_sock.close()
                clients.pop(client_sock, None)
                print(f"[CLOSED] Menunggu client baru...")

    except KeyboardInterrupt:
        print("\n[STOPPED] Server mati.")

    finally:
        server_sock.close()


if __name__ == '__main__':
    start_server()