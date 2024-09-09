# Troubleshooting Storage Tools Device

## Basic Trouble shooting

* Ssh into your device and review the docker logs.

```bash

docker logs storage_tools_device
```

A working system should start with these debug messages.  The server in this example is running at `http://127.0.0.1:8092`.

```bash
% device/app.py -c /home/devscanner/app/config/config.yaml
DEBUG :: 2024-09-09 16:08:19 :: app.py:303 :: __init__ :: Setting source name to DEV-a0291955017f 
DEBUG :: 2024-09-09 16:08:19 :: app.py:906 :: run :: Sleeping.... 
DEBUG :: 2024-09-09 16:08:24 :: app.py:366 :: _find_server :: Testing 127.0.0.1:8092 
DEBUG :: 2024-09-09 16:08:24 :: app.py:368 :: _find_server :: Connected to 127.0.0.1:8092 
DEBUG :: 2024-09-09 16:08:24 :: app.py:910 :: run :: loops 
DEBUG :: 2024-09-09 16:08:24 :: app.py:495 :: _scan :: Scanning for files 
DEBUG :: 2024-09-09 16:08:24 :: app.py:501 :: _scan :: Scanning /media/norm/Extreme SSD/airlab/volume3/chiron/datasets/experiments/ 
DEBUG :: 2024-09-09 16:08:25 :: app.py:526 :: _scan :: scan complete 
```

The format of the error message log is DEBUG :: {TIME} :: {filename:line_no} :: {function} :: {Error text}

### emitFiles :: No fies to send

The Device did not find any files in the watched directories.  Check for the error message in `_scan`.  Verify that the path is spelled correctly.

### Invalid config option 'local_tz'

`__init__ :: Invalid config option 'local_tz'. The string 'America/New_York_CITY' is not a valid time zone`

The `local_tz` set in config yaml file is not a valid time zone. Refer to [the wikipedia page on time zones](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) for the complete list.

### Can't find a server

The Storage Tools Device will attempt to connect to each of the servers listed in the `server` list in the config yaml file.  It will try to use ZeroConfig to find servers on the local network.  

### The server host is set, but not found

Verify that the Device has a route to the Server, via `ping`, `ssh`, or `telnet`.

### The server is using ZeroConf, but the Device isn't finding it

Verify that the Server is running.  

Verify that the Device and the Server are on the same network.  Some routers can filter out the UPnP messages used by ZeroConfig.
