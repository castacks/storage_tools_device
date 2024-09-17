import re
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


from SocketIOTQDM import SocketIOTQDM
from debug_print import debug_print

import psutil
import hashlib
import json
import os


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
    try:
        json.dump(rtn, open(cache_name, "w"))
        os.chmod(cache_name, 0o777 )

    except PermissionError as e:
        debug_print(f"Failed to write [{cache_name}]. Permission Denied")
    except Exception as e:
        debug_print(f"Error writing [{cache_name}]: {e}")

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

    name = hashlib.sha256("_".join(macs).encode()).hexdigest()[:16]
    rtn = f"DEV-{name}"

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


