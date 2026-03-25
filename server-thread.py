import socket
import json
import os
import struct
import threading

HOST = '127.0.0.1'
PORT = 5000
SERVER_FILES_DIR = 'server_files'
BUFFER_SIZE = 4096

clients = {}
clients_lock = threading.Lock()


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
        return True
    except:
        return False


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


def add_client(sock, username, addr):
    with clients_lock:
        clients[sock] = {
            "username": username,
            "address": addr
        }


def remove_client(sock, announce=True):
    username = None

    with clients_lock:
        if sock in clients:
            username = clients[sock]["username"]
            clients.pop(sock, None)

    try:
        sock.close()
    except:
        pass

    if username:
        print(f"[DISCONNECT] {username}")

        if announce:
            broadcast({
                "type": "info",
                "message": f"{username} left the chat"
            })


def broadcast(data, exclude_sock=None):
    with clients_lock:
        sockets = list(clients.keys())

    disconnected = []

    for client_sock in sockets:
        if client_sock == exclude_sock:
            continue

        try:
            send_json(client_sock, data)
        except:
            disconnected.append(client_sock)

    return disconnected


def start_upload(client_sock, message):
    with clients_lock:
        client = clients.get(client_sock)
        if client is None:
            return
        username = client["username"]

    filename = os.path.basename(message.get("filename"))
    filesize = message.get("filesize")
    filepath = os.path.join(SERVER_FILES_DIR, filename)

    send_json(client_sock, {"type": "upload_ready", "filename": filename})
    print(f"[UPLOAD-START] {username} -> {filename} ({filesize} bytes)")

    if recv_file_bytes(client_sock, filepath, filesize):
        finish_upload(client_sock, filename)
    else:
        send_json(client_sock, {"type": "error", "message": "Upload gagal"})
        if os.path.exists(filepath):
            os.remove(filepath)


def finish_upload(client_sock, filename):
    with clients_lock:
        client = clients.get(client_sock)
        if client is None:
            return
        username = client["username"]

    send_json(client_sock, {
        "type": "upload_done",
        "filename": filename,
        "message": "Upload berhasil"
    })

    print(f"[UPLOAD-DONE] {username} -> {filename}")

    broadcast({
        "type": "info",
        "message": f"{username} uploaded {filename}"
    })


def handle_command(client_sock, username, message):
    command = message.get("command")

    if command == "list":
        try:
            files = sorted(os.listdir(SERVER_FILES_DIR))
            send_json(client_sock, {"type": "list_result", "files": files})
        except Exception as e:
            send_json(client_sock, {
                "type": "error",
                "message": f"Gagal list: {e}"
            })

    elif command == "upload":
        start_upload(client_sock, message)

    elif command == "download":
        filename = os.path.basename(message.get("filename"))
        filepath = os.path.join(SERVER_FILES_DIR, filename)

        if not os.path.exists(filepath):
            send_json(client_sock, {
                "type": "error",
                "message": "File tidak ditemukan"
            })
            return

        send_json(client_sock, {
            "type": "download_ready",
            "filename": filename,
            "filesize": os.path.getsize(filepath)
        })

        send_file_bytes(client_sock, filepath)

        print(f"[DOWNLOAD] {username} <- {filename}")


def client_thread(client_sock, addr):
    username = None

    try:
        join_msg = recv_json(client_sock)
        if not join_msg:
            return

        username = join_msg.get("username", f"{addr[0]}:{addr[1]}")
        add_client(client_sock, username, addr)

        print(f"[CONNECT] {username} from {addr}")

        send_json(client_sock, {
            "type": "info",
            "message": f"Welcome, {username}"
        })

        broadcast({
            "type": "info",
            "message": f"{username} joined the chat"
        }, exclude_sock=client_sock)

        while True:
            message = recv_json(client_sock)
            if not message:
                break

            msg_type = message.get("type")

            if msg_type == "chat":
                print(f"[CHAT] {username}: {message.get('message')}")

                broadcast({
                    "type": "chat",
                    "sender": username,
                    "message": message.get("message")
                })

            elif msg_type == "command":
                handle_command(client_sock, username, message)

    except Exception as e:
        print(f"[ERROR] {username or addr}: {e}")

    remove_client(client_sock, announce=True)


def start_server():
    ensure_server_dir()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(20)

    print(f"[STARTED] Thread server running on {HOST}:{PORT}")

    try:
        while True:
            client_sock, addr = server_sock.accept()

            threading.Thread(
                target=client_thread,
                args=(client_sock, addr),
                daemon=True
            ).start()

    except KeyboardInterrupt:
        print("\n[STOPPED] Server dihentikan.")

    finally:
        server_sock.close()


if __name__ == '__main__':
    start_server()