import socket
import json
import os
import struct
import select

HOST = '127.0.0.1'
PORT = 5000
SERVER_FILES_DIR = 'server_files'
BUFFER_SIZE = 4096

clients = {}
fd_to_socket = {}


def ensure_server_dir():
    if not os.path.exists(SERVER_FILES_DIR):
        os.makedirs(SERVER_FILES_DIR)


def send_json(sock, data):
    payload = json.dumps(data).encode('utf-8')
    header = struct.pack('>I', len(payload))
    sock.sendall(header + payload)


def add_client(sock, addr):
    clients[sock] = {
        "username": None,
        "address": addr,
        "json_buffer": b"",
        "expected_json_length": None,
        "state": "json",
        "upload_file": None,
        "upload_filename": None,
        "upload_remaining": 0,
        "download_file": None,
        "download_remaining": 0
    }


def cleanup_upload_state(client):
    if client["upload_file"] is not None:
        client["upload_file"].close()
    client["upload_file"] = None
    client["upload_filename"] = None
    client["upload_remaining"] = 0
    client["state"] = "json"


def cleanup_download_state(client):
    if client["download_file"] is not None:
        client["download_file"].close()
    client["download_file"] = None
    client["download_remaining"] = 0
    client["state"] = "json"


def remove_client(sock, poller, announce=True):
    client = clients.get(sock)
    username = None

    if client is not None:
        username = client["username"]

        if client["upload_file"] is not None:
            filename = client["upload_filename"]
            cleanup_upload_state(client)

            if filename:
                filepath = os.path.join(SERVER_FILES_DIR, filename)
                try:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                except:
                    pass

        if client["download_file"] is not None:
            cleanup_download_state(client)

        clients.pop(sock, None)

    try:
        fd = sock.fileno()
        poller.unregister(fd)
        fd_to_socket.pop(fd, None)
    except:
        pass

    try:
        sock.close()
    except:
        pass

    if username:
        print(f"[DISCONNECT] {username}")
        if announce:
            disconnected = broadcast({
                "type": "info",
                "message": f"{username} left the chat"
            })
            for dead_sock in disconnected:
                remove_client(dead_sock, poller, announce=True)


def broadcast(data, exclude_sock=None):
    disconnected = []

    for client_sock in list(clients.keys()):
        if client_sock == exclude_sock or clients[client_sock]["username"] is None:
            continue

        try:
            send_json(client_sock, data)
        except:
            disconnected.append(client_sock)

    return disconnected


def parse_json_messages(client):
    messages = []

    while True:
        if client["expected_json_length"] is None:
            if len(client["json_buffer"]) >= 4:
                header = client["json_buffer"][:4]
                client["json_buffer"] = client["json_buffer"][4:]
                client["expected_json_length"] = struct.unpack('>I', header)[0]
            else:
                break

        if client["expected_json_length"] is not None:
            if len(client["json_buffer"]) >= client["expected_json_length"]:
                payload = client["json_buffer"][:client["expected_json_length"]]
                client["json_buffer"] = client["json_buffer"][client["expected_json_length"]:]
                client["expected_json_length"] = None

                try:
                    messages.append(json.loads(payload.decode('utf-8')))
                except:
                    continue
            else:
                break

    return messages


def start_upload(client_sock, message):
    client = clients[client_sock]
    filename, filesize = message.get("filename"), message.get("filesize")

    safe_filename = os.path.basename(filename)
    filepath = os.path.join(SERVER_FILES_DIR, safe_filename)

    try:
        file_obj = open(filepath, 'wb')
    except OSError as e:
        send_json(client_sock, {"type": "error", "message": f"Gagal membuka file: {e}"})
        return

    client["state"] = "upload"
    client["upload_file"] = file_obj
    client["upload_filename"] = safe_filename
    client["upload_remaining"] = filesize

    send_json(client_sock, {"type": "upload_ready", "filename": safe_filename})
    print(f"[UPLOAD-START] {client['username']} -> {safe_filename} ({filesize} bytes)")


def finish_upload(client_sock):
    client = clients[client_sock]
    filename = client["upload_filename"]

    cleanup_upload_state(client)

    send_json(client_sock, {
        "type": "upload_done",
        "filename": filename,
        "message": "Upload berhasil"
    })

    print(f"[UPLOAD-DONE] {client['username']} -> {filename}")

    return broadcast({
        "type": "info",
        "message": f"{client['username']} uploaded {filename}"
    })


def handle_command(client_sock, message, poller):
    client = clients[client_sock]
    command = message.get("command")

    if command == "list":
        try:
            send_json(client_sock, {
                "type": "list_result",
                "files": sorted(os.listdir(SERVER_FILES_DIR))
            })
        except OSError as e:
            send_json(client_sock, {"type": "error", "message": f"Error: {e}"})

    elif command == "upload":
        start_upload(client_sock, message)

    elif command == "download":
        filename = message.get("filename")
        safe_filename = os.path.basename(filename)
        filepath = os.path.join(SERVER_FILES_DIR, safe_filename)

        if not os.path.exists(filepath):
            send_json(client_sock, {
                "type": "error",
                "message": f"File '{safe_filename}' tidak ditemukan"
            })
            return

        try:
            file_obj = open(filepath, 'rb')
            filesize = os.path.getsize(filepath)

            client["state"] = "download"
            client["download_file"] = file_obj
            client["download_remaining"] = filesize

            send_json(client_sock, {
                "type": "download_ready",
                "filename": safe_filename,
                "filesize": filesize
            })

            poller.modify(
                client_sock.fileno(),
                select.POLLIN | select.POLLOUT | select.POLLHUP | select.POLLERR
            )

            print(f"[DOWNLOAD-START] {client['username']} <- {safe_filename} ({filesize} bytes)")

        except Exception as e:
            send_json(client_sock, {
                "type": "error",
                "message": f"Error membaca file: {e}"
            })


def handle_message(client_sock, message, poller):
    client = clients[client_sock]

    if client["username"] is None:
        username = message.get(
            "username",
            f"{client['address'][0]}:{client['address'][1]}"
        )
        client["username"] = username

        print(f"[CONNECT] {username} from {client['address']}")

        send_json(client_sock, {"type": "info", "message": f"Welcome, {username}"})

        disconnected = broadcast({
            "type": "info",
            "message": f"{username} joined the chat"
        }, exclude_sock=client_sock)

        for dead_sock in disconnected:
            remove_client(dead_sock, poller, announce=True)

        return

    msg_type = message.get("type")

    if msg_type == "chat":
        text = message.get("message", "").strip()

        if text:
            print(f"[CHAT] {client['username']}: {text}")

            disconnected = broadcast({
                "type": "chat",
                "sender": client["username"],
                "message": text
            })

            for dead_sock in disconnected:
                remove_client(dead_sock, poller, announce=True)

    elif msg_type == "command":
        handle_command(client_sock, message, poller)


def handle_upload_bytes(client_sock, data, poller):
    client = clients[client_sock]

    to_write = min(len(data), client["upload_remaining"])
    chunk = data[:to_write]
    leftover = data[to_write:]

    try:
        client["upload_file"].write(chunk)
    except OSError as e:
        send_json(client_sock, {"type": "error", "message": f"Gagal menulis file: {e}"})

        filename = client["upload_filename"]
        cleanup_upload_state(client)

        if filename:
            filepath = os.path.join(SERVER_FILES_DIR, filename)
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except:
                pass
        return

    client["upload_remaining"] -= len(chunk)
    disconnected = finish_upload(client_sock) if client["upload_remaining"] == 0 else []

    if leftover:
        client["json_buffer"] += leftover

        if client["state"] == "json":
            messages = parse_json_messages(client)
            for msg in messages:
                handle_message(client_sock, msg, poller)

    for dead_sock in disconnected:
        remove_client(dead_sock, poller, announce=True)


def handle_download_bytes(client_sock, poller):
    client = clients[client_sock]

    if client["state"] != "download":
        poller.modify(
            client_sock.fileno(),
            select.POLLIN | select.POLLHUP | select.POLLERR
        )
        return

    try:
        chunk = client["download_file"].read(BUFFER_SIZE)

        if not chunk:
            cleanup_download_state(client)

            poller.modify(
                client_sock.fileno(),
                select.POLLIN | select.POLLHUP | select.POLLERR
            )

            print(f"[DOWNLOAD-DONE] {client['username']} selesai mendownload.")
            return

        sent = client_sock.send(chunk)
        client["download_remaining"] -= sent

        if sent < len(chunk):
            client["download_file"].seek(
                client["download_file"].tell() - (len(chunk) - sent)
            )

        if client["download_remaining"] <= 0:
            cleanup_download_state(client)

            poller.modify(
                client_sock.fileno(),
                select.POLLIN | select.POLLHUP | select.POLLERR
            )

            print(f"[DOWNLOAD-DONE] {client['username']} selesai mendownload.")

    except BlockingIOError:
        pass
    except Exception:
        remove_client(client_sock, poller, announce=True)


def start_server():
    ensure_server_dir()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(20)
    server_sock.setblocking(False)

    poller = select.poll()
    server_fd = server_sock.fileno()

    poller.register(server_fd, select.POLLIN)
    fd_to_socket[server_fd] = server_sock

    print(f"[STARTED] Poll server running on {HOST}:{PORT}")

    try:
        while True:
            events = poller.poll()

            for fd, event in events:
                sock = fd_to_socket.get(fd)

                if not sock:
                    continue

                if sock is server_sock:
                    if event & select.POLLIN:
                        try:
                            client_sock, addr = server_sock.accept()
                            client_sock.setblocking(False)

                            add_client(client_sock, addr)

                            client_fd = client_sock.fileno()
                            fd_to_socket[client_fd] = client_sock

                            poller.register(
                                client_fd,
                                select.POLLIN | select.POLLHUP | select.POLLERR
                            )
                        except:
                            pass
                    continue

                if event & (select.POLLHUP | select.POLLERR | select.POLLNVAL):
                    remove_client(sock, poller, announce=True)
                    continue

                if event & select.POLLIN:
                    try:
                        data = sock.recv(BUFFER_SIZE)
                    except OSError:
                        remove_client(sock, poller, announce=True)
                        continue

                    if not data:
                        remove_client(sock, poller, announce=True)
                        continue

                    client = clients[sock]

                    if client["state"] == "upload":
                        handle_upload_bytes(sock, data, poller)
                    else:
                        client["json_buffer"] += data
                        messages = parse_json_messages(client)

                        for message in messages:
                            handle_message(sock, message, poller)

                            if client["state"] == "upload" and len(client["json_buffer"]) > 0:
                                leftover_data = client["json_buffer"]
                                client["json_buffer"] = b""

                                handle_upload_bytes(sock, leftover_data, poller)
                                break

                if event & select.POLLOUT:
                    if sock in clients and clients[sock]["state"] == "download":
                        handle_download_bytes(sock, poller)

    except KeyboardInterrupt:
        print("\n[STOPPED] Server dihentikan.")

    finally:
        for sock in list(clients.keys()):
            try:
                sock.close()
            except:
                pass

        server_sock.close()


if __name__ == '__main__':
    start_server()