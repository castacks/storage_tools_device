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

### Device is always rescanning files, even when nothing has changed

Verify that the artifacts files are being created. The Device user might not have write permission in the data directory.

The Storage Tools Device creates additional files to speed up processing.  These will always in the form of `{original_file_name}.md5` and `{original_file_name}.metadata`. These files will only be created in the `watch` directories as defined by the config yaml file.  These files can be safely removed. They will be regenerated when the system scans again.

### The console is reporting "write() before start_response"

If you see this error message on the console:

```python
Error on request:
Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/werkzeug/serving.py", line 370, in run_wsgi
    execute(self.server.app)
  File "/usr/local/lib/python3.12/site-packages/werkzeug/serving.py", line 336, in execute
    write(b"")
  File "/usr/local/lib/python3.12/site-packages/werkzeug/serving.py", line 261, in write
    assert status_set is not None, "write() before start_response"
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
```

You can safely ignore it. This message is caused by a user reloading the dashboard.
