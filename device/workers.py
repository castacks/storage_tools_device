import os
import json
import urllib
import requests
import xxhash

from datetime import datetime

import device.reindexMCAP as reindexMCAP
from device.debug_print import debug_print
from device.utils import getDateFromFilename, getMetaData


class SendWorkerArg:
    def __init__(self, message_queue, dirroot, relative_path, upload_id, offset_b, file_size, signal, server, send_offsets, split_size_gb, api_key_token, name, url, source, read_size_b) -> None:
        self.message_queue = message_queue
        self.dirroot = dirroot
        self.relative_path = relative_path
        self.upload_id = upload_id
        self.offset_b = offset_b
        self.file_size = file_size
        self.signal = signal
        self.server = server
        self.send_offsets = send_offsets
        self.split_size_gb = split_size_gb
        self.api_key_token = api_key_token
        self.name = name
        self.url = url
        self.source = source
        self.read_size_b = read_size_b


def send_worker(args):
    if args.signal.is_set():
        return fullpath, False

    assert( isinstance(args, SendWorkerArg))

    fullpath = os.path.join(args.dirroot, args.relative_path)

    if not os.path.exists(fullpath):
        debug_print(f"{fullpath} not found")
        return fullpath, False

    with open(fullpath, 'rb') as file:
        params = {}
        if args.offset_b > 0:
            file.seek(args.offset_b)
            params["offset"] = args.offset_b
            args.file_size -= args.offset_b

        args.send_offsets[args.upload_id] = args.offset_b

        split_size_b = 1024*1024*1024*args.split_size_gb
        splits = args.file_size // split_size_b

        params["splits"] = splits

        headers = {
            'Content-Type': 'application/octet-stream',
            "X-Api-Key": args.api_key_token
            }

        def read_and_update(upload_id:str, parent:SendWorkerArg):
            read_count = 0
            # while parent.isConnected(args.server) and not args.signal.is_set():
            while True:
                chunk = file.read(args.read_size_b)
                if not chunk:
                    break
                yield chunk

                # Update the progress bars
                chunck_size = len(chunk)
                args.message_queue.put({"main_pbar": chunck_size})
                args.message_queue.put({"child_pbar": args.name, "size": chunck_size, "action": "update", "total_size": args.file_size, "desc": desc})

                parent.send_offsets[upload_id] += chunck_size
                read_count += chunck_size

                if read_count >= split_size_b:
                    break

        # debug_print(f"{file_size} {splits}")
        desc = "Sending " + os.path.basename(args.relative_path)
        args.message_queue.put({"child_pbar": args.name, "desc": desc, "size": args.file_size, "action": "start"})

        # with requests.Session() as session:
        for cid in range(1+splits):

            if args.signal.is_set():
                break
            params["offset"] = args.send_offsets[args.upload_id]
            params["cid"] = cid
            # Make the POST request with the streaming data
            response = requests.post(args.url + f"/{args.source}/{args.upload_id}", params=params, data=read_and_update(args.upload_id, args), headers=headers)
            if response.status_code != 200:
                debug_print(f"Error! {response.status_code} {response.content.decode()}")
                break

        del args.send_offsets[args.upload_id]
        args.message_queue.put({"child_pbar": args.name, "action": "close"})

    return fullpath, True


def hash_worker(args):
        message_queue, entry, chunk_size = args
        if entry is None:
            debug_print("empty entry")

        if "filename" not in entry:
            debug_print("No filename")
            return entry

        filename = os.path.join(entry["dirroot"], entry["filename"])
        if not os.path.exists(filename):
            debug_print(f"File {filename} does not exist!")
            return None

        size = os.path.getsize(filename)
        cache_name = filename + ".md5"
        if os.path.exists(cache_name) and os.path.getmtime(cache_name) > os.path.getmtime(filename):
            entry["md5"] = json.load(open(cache_name))
            message_queue.put({"main_pbar": size})
            return entry

        x = xxhash.xxh128()
        name = urllib.parse.quote(filename).replace("/", "_")

        try:
            with open(filename, 'rb') as f:
                desc = os.path.basename(filename)
                message_queue.put({"child_pbar": name, "desc": desc, "size": size, "action": "start"})

                while chunk := f.read(chunk_size):
                    x.update(chunk)
                    update = len(chunk)
                    message_queue.put({"main_pbar": update})
                    message_queue.put({"child_pbar": name, "size": update, "action": "update"})

                message_queue.put({"child_pbar": name, "action": "close"})
        except Exception as e:
            debug_print(f"Caught exception {e}")

        entry["md5"] = x.hexdigest()
        with open(cache_name, "w") as fid:
            json.dump(entry["md5"], fid)

        # debug_print(f"exit {os.path.basename(filename)}")
        return entry

def create_device_entry(fullpath, filename, dirroot, size, robot_name, local_tz):
    metadata = getMetaData(fullpath, local_tz)
    if metadata is None:
        return None

    formatted_date = getDateFromFilename(fullpath)
    if formatted_date is None:
        creation_date = datetime.fromtimestamp(os.path.getmtime(fullpath))
        formatted_date = creation_date.strftime("%Y-%m-%d %H:%M:%S")
    start_time = metadata.get("start_time", formatted_date)
    end_time = metadata.get("end_time", formatted_date)

    device_entry = {
        "dirroot": dirroot,
        "filename": filename,
        "size": size,
        "start_time": start_time,
        "end_time": end_time,
        "site": "default",
        "robot_name": robot_name,
        "md5": None
    }
    device_entry.update(metadata)
    return device_entry


def metadata_worker(args):
    message_queue, dirroot, filename, fullpath, robot_name, local_tz, updates = args

    if not os.path.exists(fullpath):
        message_queue.put({"main_pbar": 1})
        debug_print(f"File not found: {fullpath}")
        return None

    size = os.path.getsize(fullpath)
    metadata_filename = fullpath + ".metadata"
    if os.path.exists(metadata_filename) and (os.path.getmtime(metadata_filename) > os.path.getmtime(fullpath)):
        try:
            device_entry = json.load(open(metadata_filename, "r"))
            if device_entry["site"] == None:
                device_entry["site"] = "default"
            if "filename" not in device_entry:
                device_entry["filename"] = filename
                device_entry["dirroot"] = dirroot
            device_entry["robot_name"] = robot_name
        except json.decoder.JSONDecodeError:
            device_entry = create_device_entry(fullpath, filename, dirroot, size, robot_name, local_tz)
            if device_entry is None:
                message_queue.put({"main_pbar": size})
                return None
    else:

        device_entry = create_device_entry(fullpath, filename, dirroot, size, robot_name, local_tz)
        if device_entry is None:
            message_queue.put({"main_pbar": size})
            return

    if filename in updates:
        device_entry.update( updates[filename])

    try:
        with open(metadata_filename, "w") as fid:
            json.dump(device_entry, fid, indent=True)
        os.chmod(metadata_filename, 0o777)

    except PermissionError as e:
        debug_print(f"Failed to write [{metadata_filename}]. Permission Denied")
    except Exception as e:
        debug_print(f"Error writing [{metadata_filename}]: {e}")

    message_queue.put({"main_pbar": size})

    return device_entry


def reindex_worker(args):
    message_queue, filename = args
    size = os.path.getsize(filename)

    status, msg = reindexMCAP.recover_mcap(filename)
    if not status:
        debug_print(msg)

    message_queue.put({"main_pbar": size})
    return filename, status