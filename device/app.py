#!/usr/bin/env python

from datetime import datetime, timedelta,timezone
import shutil 
import socketio
from threading import Thread
import socketio.exceptions
import json 
import os
import queue 
import requests
import socket
import time
import yaml
import hashlib 
import psutil
from mcap.reader import make_reader
import exifread 
import ffmpeg 
import re
import pytz 
import pathlib
from rosbags.highlevel import AnyReader
from typing import cast
from zeroconf import (
    ServiceBrowser,
    ServiceStateChange,
    Zeroconf,
)




from debug_print import debug_print
from SocketIOTQDM import SocketIOTQDM


## inspection
def getDateFromFilename(full_filename:str):


    # YYYY-MM-DD_HH.MM.SS
    pattern1 = r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})\.(\d{2})\.(\d{2})\..*$"

    match = re.match(pattern1, os.path.basename(full_filename))
    if match:
        year, month, day, hh, mm, ss = match.groups()
        return f"{year}-{month}-{day} {hh}:{mm}:{ss}"

    # *YYYYMMDD_HHMMSS.###
    pattern3 = r".*(\d{8})_(\d{6})\..{3}$"
    match = re.match(pattern3, os.path.basename(full_filename))
    if match:
        year = match.group(1)[:4]
        month = match.group(1)[4:6]
        day = match.group(1)[6:8]

        hh = match.group(2)[:2]
        mm = match.group(2)[2:4]
        ss = match.group(2)[4:]
        return f"{year}-{month}-{day} {hh}:{mm}:{ss}"

    # *YYYYMMDD*
    pattern2 = r".*(\d{8})_*$"
    match = re.match(pattern2, full_filename)
    if match:
        yy = match.group(1)[:4]
        mm = match.group(1)[4:6]
        dd = match.group(1)[6:8]
    
        return f"{yy}-{mm}-{dd} 00:00:00"
    

    # *YYYY_MM_DD-HH_MM_SS*
    pattern4 = r"^.*(\d{4})_(\d{2})_(\d{2})-(\d{2})_(\d{2})_(\d{2}).*$"
    match = re.match(pattern4, os.path.basename(full_filename))
    if match:
        year, month, day, hh, mm, ss = match.groups()
        return f"{year}-{month}-{day} {hh}:{mm}:{ss}"

    match = re.match(pattern4, os.path.basename(os.path.dirname(full_filename)))
    if match:
        year, month, day, hh, mm, ss = match.groups()
        return f"{year}-{month}-{day} {hh}:{mm}:{ss}"

    return None

def _getMetaDataMCAP(filename: str, local_tz:str) -> dict:
    with open(filename, "rb") as f:
        reader = make_reader(f)

        summary = reader.get_summary()

        if summary.statistics.message_end_time == 0:
            return None

        start_time_ros = summary.statistics.message_start_time
        end_time_ros = summary.statistics.message_end_time
        rtn = {
            "start_time": datetime.fromtimestamp(start_time_ros // 1e9, tz=timezone.utc).astimezone(pytz.timezone(local_tz)).strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": datetime.fromtimestamp(end_time_ros // 1e9, tz=timezone.utc).astimezone(pytz.timezone(local_tz)).strftime("%Y-%m-%d %H:%M:%S"),
            "topics": [ channel.topic for _, channel in summary.channels.items()]
        }
    return rtn


def _getMetadataROS(filename: str, local_tz:str) -> dict:
    reader = AnyReader([pathlib.Path(filename)])
    reader.open()

    start_time_ros = reader.start_time
    end_time_ros = reader.end_time 
    topics = sorted(reader.topics)

    rtn = {
        "start_time": datetime.fromtimestamp(start_time_ros // 1e9, tz=timezone.utc).astimezone(pytz.timezone(local_tz)).strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": datetime.fromtimestamp(end_time_ros // 1e9, tz=timezone.utc).astimezone(pytz.timezone(local_tz)).strftime("%Y-%m-%d %H:%M:%S"),
        "topics": topics
    }

    return rtn 

def _getMetaDataJPEG(filename: str) -> dict:

    formatted_date =  getDateFromFilename(filename)
    if formatted_date:
        return {
            "start_time": formatted_date,
            "end_time": formatted_date
            }


    with open(filename, "rb") as f:
        tags = exifread.process_file(f)
    
    rtn = {}
    for tag in tags:
        if tag == "EXIF DateTimeOriginal":
            timestamp_str = str(tags[tag])
            rtn["start_time"] = timestamp_str
            rtn["end_time"] = timestamp_str
    return rtn

def _getMetaDataMP4(filename: str) -> dict:
    try:
        probe = ffmpeg.probe(filename)
        if "streams" not in probe:
            return {}
        if "tags" not in probe["streams"][0]:
            return {}
        if "creation_time" not in  probe["streams"][0]["tags"]:
            return  {}


        formatted_date =  getDateFromFilename(filename)
        if formatted_date:
            creation_time = formatted_date
        else:
            creation_time = probe["streams"][0]["tags"]["creation_time"]
        duration = float(probe['format']['duration'])
        
        # Convert creation time to datetime object
        creation_datetime = datetime.fromisoformat(creation_time.replace('Z', '+00:00'))
        
        # Calculate end time by adding duration
        end_datetime = creation_datetime + timedelta(seconds=duration)
  
        rtn = {
            "start_time": creation_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end_datetime.strftime("%Y-%m-%d %H:%M:%S")
        }
        return rtn 

    except ffmpeg.Error as e:
        # invalid file!
        return None

def _getMetaDataPNG(filename:str) -> dict:
    formatted_date =  getDateFromFilename(filename)
    if formatted_date is None:

        creation_date = datetime.fromtimestamp(os.path.getmtime(filename))
        formatted_date = creation_date.strftime("%Y-%m-%d %H:%M:%S")

    return {
        "start_time": formatted_date,
        "end_time": formatted_date
        }


def _getMetaDataText(filename: str) -> dict:
    # have to use the file time for this one

    formatted_date =  getDateFromFilename(filename)
    if formatted_date is None:

        creation_date = datetime.fromtimestamp(os.path.getmtime(filename))
        formatted_date = creation_date.strftime("%Y-%m-%d %H:%M:%S")

    return {
        "start_time": formatted_date,
        "end_time": formatted_date
        }


def getMetaData(filename:str, local_tz:str) -> dict:
    if filename.lower().endswith(".mcap"):
        return _getMetaDataMCAP(filename, local_tz)
    if filename.lower().endswith(".bag"):
        return _getMetadataROS(filename, local_tz)
    elif filename.lower().endswith(".jpg"):
        return _getMetaDataJPEG(filename)
    elif filename.lower().endswith(".mp4"):
        return _getMetaDataMP4(filename)
    elif filename.lower().endswith(".txt"):
        return _getMetaDataText(filename)
    elif filename.lower().endswith(".ass"):
        return _getMetaDataText(filename)
    elif filename.lower().endswith(".png"):
        return _getMetaDataPNG(filename)
    elif filename.lower().endswith(".yaml"):
        return _getMetaDataText(filename)
    return {}





def compute_md5(file_path, chunk_size=8192, position=None, socket=None, source=None, main_pbar=None):
    md5 = hashlib.md5()
    try:
        pbar = None 

        cache_name = file_path + ".md5"
        if os.path.exists(cache_name) and os.path.getmtime(cache_name) > os.path.getmtime(file_path):
            rtn = json.load(open(cache_name))
            if main_pbar: main_pbar.update(os.path.getsize(file_path))
            return rtn 
            
        with open(file_path, 'rb') as f:
            if position:
                size = os.path.getsize(file_path)
                pbar = SocketIOTQDM(total=size, unit="B", unit_scale=True, leave=False, position=position, delay=1, desc=os.path.basename(file_path), source=source, socket=socket,event="device_status_tqdm" )
            while chunk := f.read(chunk_size):
                md5.update(chunk)
                if pbar: pbar.update(len(chunk))
                if main_pbar: main_pbar.update(len(chunk))

            if pbar: pbar.close()
    except FileNotFoundError:
        return None  # Handle file not found if needed
    
    rtn = md5.hexdigest()
    json.dump(rtn, open(cache_name, "w"))
    return rtn


def get_source_by_mac_address():
    macs = []
    addresses = psutil.net_if_addrs()
    for interface in sorted(addresses):
        if interface == "lo":
            continue
        for addr in sorted(addresses[interface]):
            if addr.family == psutil.AF_LINK:  # Check if it's a MAC address
                if psutil.net_if_stats()[interface].isup:
                    macs.append(addr.address.replace(":",""))
    rtn = "DEV-" + "_".join(macs)
    return rtn 


"""
This class represents a device that can scan for files, send them to a server, and interact with the server.

Attributes:
    m_config (dict): The configuration of the device, loaded from a YAML file.
"""
class Device:
    def __init__(self, filename: str) -> None:
        """
        Initialize the Device object with a configuration file.

        Args:
            filename (str): The path to the configuration file.
        """
        self.m_config = None
        with open(filename, "r") as f:
            self.m_config = yaml.safe_load(f)

        self.m_config["source"] = get_source_by_mac_address()
        debug_print(f"Setting source name to {self.m_config['source']}")

        self.m_config["servers"] = self.m_config.get("servers", [])
        self.m_sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=0,  # Infinite attempts
            reconnection_delay=1,  # Start with 1 second delay
            reconnection_delay_max=5,  # Maximum 5 seconds delay
            randomization_factor=0.5,  # Randomize delays by +/- 50%
            logger=False,  # Enable logging for debugging
            engineio_logger=False  # Enable Engine.IO logging
        )

        self.m_server = None 

        self.m_signal = None
        self.m_fs_info = {}

        self.m_files = None 

        self.m_md5 = {}
        self.m_updates = {}
        self.m_computeMD5 = self.m_config.get("computeMD5", True)
        self.m_chunk_size = self.m_config.get("chunk_size", 8192)

        self.m_local_tz = self.m_config.get("local_tz", "America/New_York")

        services = ['_http._tcp.local.']
        self.m_zeroconfig = Zeroconf()
        self.m_zero_conf_name = "Airlab_storage._http._tcp.local."
        self.browser = ServiceBrowser(self.m_zeroconfig, services, handlers=[self.on_change])

    def on_change(self, zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        if name != self.m_zero_conf_name:
            return 
        
        if state_change is ServiceStateChange.Added:
            info = zeroconf.get_service_info(service_type, name)
            
            if info:
                addresses = [
                    "%s:%d" % (addr, cast(int, info.port))
                    for addr in info.parsed_scoped_addresses()
                ]

            for address in addresses:
                if address in self.m_config["servers"]:
                    continue
                self.m_config["servers"].append(address)
                
    def _find_server(self):
        for server_full in self.m_config["servers"]:
            try:
                self.m_server = None
                server, port = server_full.split(":")
                port = int(port)
                debug_print(f"Testing {server}:{port}")
                socket.create_connection((server, port))
                debug_print(f"Connected to {server}:{port}")

                if self.m_sio.connected:
                    self.m_sio.disconnect()

                api_key_token = self.m_config["API_KEY_TOKEN"]
                headers = {"X-Api-Key": api_key_token}

                self.m_sio.connect(f"http://{server}:{port}/socket.io", headers=headers, transports=['websocket'])
                # self.m_sio.connect(f"http://{server}:{port}/socket.io", headers=headers, transports=['polling'])
                self.m_sio.on('control_msg')(self._handle_control_msg)    
                self.m_sio.on('update_entry')(self._update_entry)
                self.m_sio.on('set_project')(self._set_project)
                self.m_sio.on('set_md5')(self._set_md5)
                self.m_sio.on("device_scan")(self.scan)
                self.m_sio.on("device_send")(self.send)
                self.m_sio.on("device_remove")(self.removeFiles)
                self.m_sio.on("keep_alive_ack")(self._keepAlive)
                self.m_sio.on("disconnect")(self._on_disconnect)
                # self.m_sio.on("echo")(self._on_echo)

                self.m_sio.emit('join', { 'room': self.m_config["source"], "type": "device" })
                self.m_server = server_full
                return server_full
            except ConnectionRefusedError as e:
                debug_print(f"Connection Refused: {e}")
                pass

            except OSError as e:
                debug_print(f"OS Error: {e}")
                pass 

            except socketio.exceptions.ConnectionError as e:
                debug_print(f"Connection error {e}")
                pass 

            except ValueError:
                self.m_sio.disconnect()
                pass

            except Exception as e:                
                debug_print(e)
                # raise e
                pass 
        return None 


    def _on_disconnect(self):        
        debug_print(f"Got disconnection on {requests}")

    def _keepAlive(self):
        pass
        #debug_print("Still alive")

    def _handle_control_msg(self, data):
        debug_print(data)
        if data.get("action", "") == "cancel":
            self.m_signal = "cancel"

    def _update_entry(self, data):
        source = data.get("source")
        if source != self.m_config["source"]:
            return 

        relpath = data.get("relpath")
        basename = data.get("basename")
        filename = os.path.join(relpath, basename)
        update = data.get("update")

        self.m_updates[filename] = self.m_updates.get(filename, {})
        self.m_updates[filename].update( update )


    def _set_project(self, data):
        debug_print(data)
        source = data.get("source")
        if source != self.m_config["source"]:
            return 

        project = data.get("project")        
        self.m_config["project"] = project

        self.emitFiles()

    def _set_md5(self, data):
        debug_print(data)
        source = data.get("source")
        if source != self.m_config["source"]:
            return 
        
        self.m_computeMD5 = data.get("value", False)


    
    def _include(self, filename: str) -> bool:
        """
        Check if a file should be included based on its suffix.

        This method checks the file's suffix against the include and exclude suffix lists in the configuration.
        If an include suffix list is present, the file must match one of the suffixes to be included.
        If an exclude suffix list is present, the file will be excluded if it matches any of the suffixes.

        Args:
            filename (str): The name of the file to check.

        Returns:
            bool: True if the file should be included, False otherwise.
        """
        if filename.startswith("."):
            return False

        if "include_suffix" in self.m_config:
            for suffix in self.m_config["include_suffix"]:
                if filename.endswith(suffix):
                    return True 
            return False
        if "exclude_suffix" in self.m_config:
            for suffix in self.m_config["exclude_suffix"]:
                if filename.endswith(suffix):
                    return False 
            return True

    def _remove_dirpath(self, filename:str):
        for dirroot in self.m_config["watch"]:
            if filename.startswith(dirroot):
                rtn = filename.replace(dirroot, "")
                return rtn.strip("/")
        return filename

    def _scan(self):
        debug_print("Scanning for files")
        self.m_sio.emit("device_status", {"source": self.m_config["source"], "msg": "Scanning for files"})
        self.m_fs_info = {}
        entries = []
        total_size = 0
        for dirroot in self.m_config["watch"]:
            debug_print("Scanning " + dirroot)

            self.m_sio.emit("device_status", {"source": self.m_config["source"], "msg": f"Scanning {dirroot} for files"})

            if os.path.exists(dirroot):
                dev = os.stat(dirroot).st_dev
                if not dev in self.m_fs_info:
                    total, used, free = shutil.disk_usage(dirroot)
                    free_percentage = (free / total) * 100
                    self.m_fs_info[dev] = (dirroot, f"{free_percentage:0.2f}")

            for root, dirs, files in os.walk(dirroot):
                # debug_print(root)
                # self.m_sio.emit("device_status", {"source": self.m_config["source"], "msg": f"Scanning {dirroot} for files"})
                for file in files:
                    if not self._include(file):
                        continue
                    filename = os.path.join(root, file).replace(dirroot, "")
                    filename = filename.strip("/")
                    fullpath = os.path.join(root, file)
                    size = os.path.getsize(fullpath)

                    metadata = getMetaData(fullpath, self.m_local_tz)
                    if metadata is None:
                        # invalid file!
                        continue 

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
                        "site": None,
                        "robot_name": None,
                        "md5": None
                    }
                    device_entry.update(metadata)

                    if filename in self.m_updates:
                        device_entry.update( self.m_updates[filename])

                    entries.append(device_entry)

                    total_size += size

        with SocketIOTQDM(total=total_size, desc="Compute MD5 sum", position=0, unit="B", unit_scale=True, leave=False, source=self.m_config["source"], socket=self.m_sio, event="device_status_tqdm") as main_pbar:

            file_queue = queue.Queue()
            rtn_queue = queue.Queue()
            for entry in entries:
                fullpath = os.path.join(entry["dirroot"], entry["filename"])

                last_modified = os.path.getmtime(fullpath)
                do_compute = False
                if fullpath not in self.m_md5:
                    do_compute = True 
                elif self.m_md5[fullpath]["last_modified"] > last_modified:
                    do_compute = True 

                if do_compute:  
                    file_queue.put((fullpath, entry))

            def worker(position:int):
                while True:
                    try:
                        fullpath, entry = file_queue.get(block=False)
                    except queue.Empty:
                        break 
                    except ValueError:
                        break

                    if self.m_computeMD5:
                        md5 = compute_md5(fullpath, self.m_chunk_size, 1+position, socket=self.m_sio, source=self.m_config["source"], main_pbar=main_pbar)
                    else:
                        md5 = "0"
                    entry["md5"] = md5
                    rtn_queue.put(entry)

            threads = []
            num_threads = min(self.m_config["threads"], len(entries))
            for i in range(num_threads):
                thread = Thread(target=worker, args=(i,))
                thread.start()
                threads.append(thread)

            for thread in threads:
                thread.join()

        rtn = []
        try:
            while not rtn_queue.empty():
                rtn.append(rtn_queue.get())
        except ValueError:
            pass

        try:
            self.m_sio.emit("device_status", {"source": self.m_config["source"]})
        except socketio.exceptions.BadNamespaceError:
            pass 

        self.m_files = rtn 
        return rtn 

    def scan(self, data):
        source = data.get("source")
        if source != self.m_config["source"]:
            return 

        self._scan()
        self.emitFiles()


    def send(self, data):
        source = data.get("source")
        if source != self.m_config["source"]:
            return 
        files = data.get("files")

        self._sendFiles(self.m_server, files)


    def removeFiles(self, data):
        # debug_print(data)
        source = data.get("source")
        if source != self.m_config["source"]:
            return 
        files = data.get("files")

        self._removeFiles(files)

    ''' @brief send files to server. 

        @arg server hosname:port
        @arg filelist list[(dirroot, relpath, upload_id, offset, size)]
    '''    
    def _sendFiles(self, server, filelist):
        self.m_signal = None 

        num_threads = min(self.m_config["threads"], len(filelist))
        url = f"http://{server}/file"

        source = self.m_config["source"]
        api_key_token = self.m_config["API_KEY_TOKEN"]

        split_size_gb = self.m_config.get("split_size_gb", 1)

        total_size = 0
        file_queue = queue.Queue()
        for file_pair in filelist:
            debug_print(f"add to queue {file_pair}")
            offset = file_pair[3]
            size = file_pair[4]
            try:
                total_size += int(size) - int(offset)
            except ValueError as e:
                debug_print(file_pair)
                raise e 
            file_queue.put(file_pair)

        with SocketIOTQDM(total=total_size, desc="File Transfer", position=0, unit="B", unit_scale=True, leave=False, source=self.m_config["source"], socket=self.m_sio, event="device_status_tqdm") as main_pbar:

            def worker(index:int):
                with requests.Session() as session:
                    while self.isConnected():
                        try:
                            dirroot, file, upload_id, offset_, total_size = file_queue.get(block=False)
                            offset_b = int(offset_)
                            total_size = int(total_size)
                        except queue.Empty:
                            break 
                        
                        if self.m_signal == "cancel":
                            break

                        fullpath = os.path.join(dirroot, file)
                        if not os.path.exists(fullpath):
                            main_pbar.update()
                            continue 

                        # total_size = os.path.getsize(fullpath)
                        

                        with open(fullpath, 'rb') as file:

                            params = {}
                            if offset_b > 0:
                                file.seek(offset_b)
                                params["offset"] = offset_b 
                                total_size -= offset_b 

                            split_size_b = 1024*1024*1024*split_size_gb
                            splits = total_size // split_size_b

                            # debug_print(f"splits: {splits}")

                            params["splits"] = splits

                            headers = {
                                'Content-Type': 'application/octet-stream',                                
                                "X-Api-Key": api_key_token
                                }
                            
                            # Setup the progress bar
                            with SocketIOTQDM(total=total_size, unit="B", unit_scale=True, leave=False, position=1+index, source=self.m_config["source"], socket=self.m_sio, event="device_status_tqdm") as pbar:
                                def read_and_update(offset_b, parent):
                                    
                                    read_count = 0
                                    while parent.isConnected():
                                        # Read the file in chunks of 4096 bytes (or any size you prefer)
                                        chunk = file.read(1024*1024)
                                        # debug_print(f"{len(chunk)}")
                                        if not chunk:
                                            break
                                        yield chunk

                                        chunck_size = len(chunk)
                                        # Update the progress bar
                                        pbar.update(chunck_size)
                                        main_pbar.update(chunck_size)

                                        if self.m_signal:
                                            if self.m_signal == "cancel":
                                                break
                                
                                        offset_b += chunck_size
                                        read_count += chunck_size

                                        if read_count >= split_size_b:
                                            break

                                for cid in range(1+splits):
                                    params["offset"] = offset_b 
                                    params["cid"] = cid
                                    # Make the POST request with the streaming data
                                    response = session.post(url + f"/{source}/{upload_id}", params=params, data=read_and_update(offset_b, self), headers=headers)

                                if response.status_code != 200:
                                    print("Error uploading file:", response.text)

                            # main_pbar.update()
        

            threads = []
            for i in range(num_threads):
                thread = Thread(target=worker, args=(i,))
                thread.start()
                threads.append(thread)

            for thread in threads:
                thread.join()

        self.m_signal = None 


    def _removeFiles(self, files):

        debug_print("Enter")
        for item in files:
            dirroot, file, upload_id = item
            fullpath = os.path.join(dirroot, file)
            
            if os.path.exists(fullpath):
                debug_print(f"Removing {fullpath}")
                # only rename for testing. 
                bak = fullpath + ".bak"
                if os.path.exists(bak): 
                    continue
                os.rename(fullpath, bak)

            md5 = fullpath + ".md5"
            if os.path.exists(md5):
                debug_print(f"Removing {md5}")
                # only rename for testing. 
                bak = md5 + ".bak"
                if os.path.exists(bak): 
                    continue
                os.rename(md5, bak)

        self._scan()
        self.emitFiles()

    def emitFiles(self):
        if self.m_files is None:
            self._scan()
        # debug_print(files)
        if len(self.m_files) == 0:
            debug_print("No files to send")
            return None 

        # clear out signals
        self.m_signal = None 

        robot_name = self.m_config.get("robot_name", None)
        project = self.m_config.get("project")
        source = self.m_config["source"]

        data = {
            "robot_name": robot_name, 
            "project": project, 
            "source": source,
            "fs_info": self.m_fs_info,
            # "files": self.m_files
            }

        # sz = len(json.dumps(data))
        # debug_print(f"sending {sz} bytes")

        if self.m_sio.connected:
            if project and len(project) > 1:
                N = 5
                packs = [self.m_files[i:i + N] for i in range(0, len(self.m_files), N)]
                # debug_print(f"sending {len(packs)}")
                for pack in packs:                
                    self.m_sio.emit("device_files_items", {"source": source, "files": pack})
                time.sleep(0.5)
            self.m_sio.emit("device_files", data)


    def isConnected(self):
        return self.m_sio.connected

    def run(self):
        try:
            while True:
                server =  self._find_server()
                if server is None:
                    debug_print("Sleeping....")
                    time.sleep(self.m_config["wait_s"])
                    continue

                debug_print("loops")
                self.emitFiles()

                trigger = 0
                while self.isConnected():
                    if trigger > 10:
                        trigger = 0                        
                        self.m_sio.emit("keep_alive")
                    else:
                        trigger +=1 
                    time.sleep(1)
                debug_print("Got disconnected!")

        except KeyboardInterrupt:
            debug_print("Terminated")
            pass          


    # def runOnce(self):

    #     try:
    #         while True:   
    #             debug_print("-----")             
    #             server =  self._find_server()
    #             if server is None:
    #                 debug_print("Sleeping....")
    #                 time.sleep(self.m_config["wait_s"])
    #                 continue
    #             else:
    #                 status = self.sendFiles(server)
    #                 if status is None:
    #                     debug_print("No files on server. Sleeping for one minute")
    #                     time.sleep(60)

    #     except KeyboardInterrupt:
    #         debug_print("Terminated")
    #         pass                  

    

if __name__ == "__main__":
    import sys 
    print("% " + " ".join(sys.argv))
    from argparse import ArgumentParser
    parser = ArgumentParser()
    parser.add_argument("-c", "--config", type=str, required=False, default="../config/config.yaml", help="Config file for this instance")
    args = parser.parse_args()

    device = Device(args.config)
    device.run()