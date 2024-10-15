import queue
import re
import socket
import time
import mcap
import mcap.exceptions
from mcap.reader import make_reader
from datetime import datetime, timezone


from rosbags.highlevel import AnyReader
import exifread 
import ffmpeg 
import pathlib
import pytz 
from datetime import datetime, timedelta,timezone



import psutil
import hashlib
import os
from queue import Queue

try:
    from SocketIOTQDM import MultiTargetSocketIOTQDM
    from debug_print import debug_print
except ModuleNotFoundError:
    from .SocketIOTQDM import MultiTargetSocketIOTQDM
    from .debug_print import debug_print
    

class PosMaker:
    def __init__(self, max_pos) -> None:
        self.m_pos = {i: False for i in range(max_pos)}
        self.m_max = max_pos
    
    def get_next_pos(self) -> int:
        for i in sorted(self.m_pos):
            if not self.m_pos[i]:
                self.m_pos[i] = True 
                return i
            
        # just in case things get messed up, always return a valid position
        i = self.m_max 
        self.m_max += 1
        self.m_pos[i] = True
        return i 
    
    def release_pos(self, i):
        self.m_pos[i] = False

def pbar_thread(messages:Queue, total_size, source, socket_events, desc, max_threads):
    pos_maker = PosMaker(max_threads)

    positions = {}

    pbars = {}
    pbars["main_pbar"] = MultiTargetSocketIOTQDM(total=total_size, unit="B", unit_scale=True, leave=False, position=0, delay=1, desc=desc, source=source,socket_events=socket_events)

    while True:
        try:
            action_msg = messages.get(block=True)

        except queue.Empty:
            time.sleep(0.001)
            continue
        except ValueError:
            time.sleep(0.001)
            continue
        
        if "close" in action_msg:
            # debug_print("close")
            break

        if "main_pbar" in action_msg:
            pbars["main_pbar"].update(action_msg["main_pbar"])
            continue

        if "child_pbar" in action_msg:
            name = action_msg["child_pbar"]
            action = action_msg["action"] 
            if action == "start":
                desc = action_msg["desc"]
                position = pos_maker.get_next_pos()
                positions[name] = position
                size = action_msg["size"]
                if position in pbars:
                    pbars[position].close()
                    del pbars[position]
                # debug_print(f"creating {name}")
                pbars[position] = MultiTargetSocketIOTQDM(total=size, unit="B", unit_scale=True, leave=False, position=position+1, delay=1, desc=desc, source=source,socket_events=socket_events)
                continue
            if action == "update":     
                # debug_print(f"updating {name}")           
                position = positions.get(name, None)
                if position == None:
                    debug_print(f"Do not have pbar for {name}")
                    for pname in positions:
                        debug_print(f"{pname} {positions[pname]}")
                    continue
                size = action_msg["size"]
                if position in pbars:
                    # debug_print(f"{position} : {size}")
                    pbars[position].update(size)
                else:
                    debug_print(f"do not have pbar for {position}")
                continue
            if action == "close":
                position = positions.get(name, None)
                if position == None:
                    continue

                if position in pbars:
                    pbars[position].close()
                    del pbars[position]
                pos_maker.release_pos(position)

                # debug_print(f"removing {name}")
                del positions[name]
                continue
            continue 


    positions = pbars.keys()
    for position in positions:
        pbars[position].close()



def get_source_by_mac_address(robot_name):
    macs = []
    addresses = psutil.net_if_addrs()
    for interface in sorted(addresses):
        if interface == "lo":
            continue
        for addr in sorted(addresses[interface]):
            if addr.family == psutil.AF_LINK:  # Check if it's a MAC address
                if psutil.net_if_stats()[interface].isup:
                    macs.append(addr.address.replace(":",""))

    name = hashlib.sha256("_".join(macs).encode()).hexdigest()[:8]
    rtn = f"DEV-{robot_name}-{name}"

    # rtn = "DEV-" + "_".join(macs)
    return rtn


def getDateFromFilename(full_filename:str):
    """
    Extracts a date and time from a given filename using various patterns.

    Args:
        full_filename (str): The full path or name of the file.

    Returns:
        str: A formatted date-time string in the form 'YYYY-MM-DD HH:MM:SS' if a matching pattern is found, 
             or None if no date can be extracted.
    """

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

        try:
            reader = make_reader(f)
        except mcap.exceptions.EndOfFile:
            return None 

        try:
            summary = reader.get_summary()
        except Exception as e:
            debug_print(f"Failed to read {filename} because {e}")
            return None

        if summary.statistics.message_end_time == 0:
            return None

        # mcap does not have a for free message count.  
        topics = {}
        for _, channel, message in reader.iter_messages():
            topic = channel.topic
            topics[topic] = topics.get(topic, 0) +1


        start_time_ros = summary.statistics.message_start_time
        end_time_ros = summary.statistics.message_end_time
        rtn = {
            "start_time": datetime.fromtimestamp(start_time_ros // 1e9, tz=timezone.utc).astimezone(pytz.timezone(local_tz)).strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": datetime.fromtimestamp(end_time_ros // 1e9, tz=timezone.utc).astimezone(pytz.timezone(local_tz)).strftime("%Y-%m-%d %H:%M:%S"),
            "topics": topics
        }
    return rtn


def _getMetadataROS(filename: str, local_tz:str) -> dict:
    reader = AnyReader([pathlib.Path(filename)])
    reader.open()

    start_time_ros = reader.start_time
    end_time_ros = reader.end_time 
    topics = {topic: reader.topics[topic].msgcount for topic in sorted(reader.topics)}

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


def get_ip_address_and_port(server_address):
    ip_address = None
    port = None 
    if ":" in server_address:
        name, port = server_address.split(":")
    else:
        name = server_address

    try:
        ip_address = socket.gethostbyname(name)
    except socket.gaierror as e:
        pass 

    return ip_address, port

def same_adddress(server_address_1, server_address_2):
    ip_address_1, port_1 = get_ip_address_and_port(server_address_1)
    ip_address_2, port_2 = get_ip_address_and_port(server_address_2)

    if not ip_address_1 or not ip_address_2:
        return False 
    
    if not port_1 or not port_2:
        return False 
    
    if ip_address_1 != ip_address_2:
        return False 
    
    return port_1 == port_2

def address_in_list(query_address, server_list):
    ip_address_1, port_1 = get_ip_address_and_port(query_address)
    if not ip_address_1 or not port_1:
        return False 
    
    found = False
    for server_address_2 in server_list:
        ip_address_2, port_2 = get_ip_address_and_port(server_address_2)
        if not ip_address_2 or not port_2:
            continue 
        if ip_address_1 == ip_address_2 and port_1 == port_2:
            found = True
            break
    return found 
