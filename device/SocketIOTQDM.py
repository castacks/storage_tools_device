import humanfriendly
import socketio.exceptions
from tqdm import tqdm
import time
# from debug_print import debug_print


'''
A socketio wrapper for tqdm.  This assumes there is 
a socketio client available. It will fallback to regular
tqdm if there isn't a socket.  

It will send messages to the provided "event". The format is 
{
  "source": str(),  # A unique name for the source of the message
  "desc": str(),    # TQDM description of the progress bar.
  "progress": any,  # Int or Float, current progress. 
  "total": any      # Int or Float, tqdm total. 
  "postion": int |None, # position for this progress bar.  Set to none to be default progress. 
  "rate", str(),    # human readable rate 
  "remaining": str()    # human readable time remaining.  
}


Example usage:

python:

import time
import socketio
from SocketIOTQDM import SocketIOTQDM

def count_up(server, port, n):
   sio = socketio.Client()
   sio.connect(f"http://{server}:{port}/socket.io")

   with SocketIOTQDM(total=n, desc="counting", source="counter", socket=sio, event="counting_tqdm" ) as pbar:
      for i in range(n): 
          pbar.update()
          time.sleep(1)

html:

<html>
<head>
<script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>

</head>
<body>
<h1> counter </h1>
<div id="counting_tqdm"></div>

<script type="text/javascript" charset="utf-8">
var socket = io.connect('http://' + document.domain + ':' + location.port)

socket.on("counting_tqdm", function(msg) {
  let div = document.getElementById("counting_tqdm");
  div.innerHTML = msg.progress + "/" + msg.total + " : " + msg.rate + " : " + msg.remaining;
});
</script>

</body>
</html>

'''

class SocketIOTQDM(tqdm):    
    def __init__(self, *args, **kwargs):
        self.room = kwargs.pop('room', None)
        self.event = kwargs.pop('event', 'progress_update')
        self.sio = kwargs.pop('socket', None)
        self.source = kwargs.pop('source', None)
        self.position = kwargs.get("position", None)
        self.emit_interval = kwargs.get("emit_interval", 1)
        self.last_emit_time = time.time()
        super().__init__(*args, **kwargs)

        if self.sio:
            msg = {
                "source": self.source,
                "desc": self.desc,
                "progress": self.n,
                "total": self.total,
                "position": self.position
            }

            try:
                if self.room:
                    self.sio.emit(self.event, msg, room=self.room)
                else:
                    self.sio.emit(self.event, msg)
            except socketio.exceptions.BadNamespaceError:
                pass

    def _emit_update(self, msg):
        if not self.sio:
            return
        current_time = time.time()
        if current_time - self.last_emit_time > self.emit_interval:
            if self.room:
                self.sio.emit(self.event, msg, room=self.room)
            else:
                self.sio.emit(self.event, msg)
            self.last_emit_time = current_time

    def update(self, n=1):
        super().update(n)
        if not self.sio:
            return

        remaining = "Estimating"
        rate = self.format_dict["rate"]
        if rate:
            remaining = (self.total - self.n) / rate
            remaining = humanfriendly.format_timespan(remaining)
        else:
            rate = 0

        if self.unit == "B":
            hrate = humanfriendly.format_size(rate) + "/S"
        else:
            hrate = humanfriendly.format_number(rate) + " it/S"

        msg = {
            "source": self.source,
            "desc": self.desc,
            "progress": self.n,
            "total": self.total,
            "position": self.position,
            "rate": hrate,
            "remaining": remaining
        }

        try:
            self._emit_update(msg)
        except socketio.exceptions.BadNamespaceError:
            pass

    def close(self):
        super().close()

        msg = {
            "source": self.source,
            "desc": self.desc,
            "progress": -1,
            "total": self.total,
            "position": self.position,
        }
        try:
            if self.room:
                self.sio.emit(self.event, msg, room=self.room)
            else:
                self.sio.emit(self.event, msg)
        except socketio.exceptions.BadNamespaceError as e:
            # got disconnected.  
            pass


class MultiTargetSocketIOTQDM(tqdm):  
    """
    A subclass of tqdm that emits progress updates to multiple Socket.IO targets. 

    It works like a SocketIOTQDM, but it can take a list of (SocketIO, event, room|None) 
    to send to multiple events or targets.  

    Attributes:
        room (str): The Socket.IO room to emit updates to.
        sio_events (list): List of Socket.IO instances and events to emit.
        source (str): Identifier for the source of progress updates.
        position (int): Position indicator for the progress bar.
        emit_interval (float): Minimum interval between emitting progress updates.
    """

    def __init__(self, *args, **kwargs):
        """
        Initializes the progress bar with specified attributes and emits initial status.

        Args:
            *args: Positional arguments for tqdm initialization.
            **kwargs: Additional arguments, including 'room', 'socket_events', 'source', and 'emit_interval'.
        """
        self.room = kwargs.pop('room', None)
        self.sio_events = kwargs.pop('socket_events', [])
        self.source = kwargs.pop('source', None)
        self.position = kwargs.get("position", None)
        self.emit_interval = kwargs.pop("emit_interval", 1)
        self.last_emit_time = time.time()
        super().__init__(*args, **kwargs)

        for (sio, event, room) in self.sio_events:
            msg = {
                "source": self.source,
                "desc": self.desc,
                "progress": self.n,
                "total": self.total,
                "position": self.position
            }

            try:
                self._emit_message(msg, sio, event, room)
            except socketio.exceptions.BadNamespaceError:
                pass

    def _emit_update(self, msg):
        """
        Emits a progress update to all specified Socket.IO events, if the emit interval has passed.

        Args:
            msg (dict): Message data to be emitted.
        """
        if len(self.sio_events) == 0:
            return 

        current_time = time.time()
        if current_time - self.last_emit_time > self.emit_interval:
            for sio, event, room in self.sio_events:

                self._emit_message(msg, sio, event, room)
            self.last_emit_time = current_time

    def _emit_message(self, msg, sio, event, room):
        """
        Emits a specific message to a Socket.IO event, with an optional room target.

        Args:
            msg (dict): Message data to emit.
            sio: Socket.IO instance.
            event (str): Event name to emit to.
            room (str or None): Optional room to target.
        """
        if room:
            sio.emit(event, msg, to=room)
        else:
            sio.emit(event, msg)
        

    def update(self, n=1):
        """
        Updates the progress bar by `n` steps and emits progress status if needed.

        Args:
            n (int): Number of steps to advance the progress bar.
        """
        super().update(n)

        if len(self.sio_events) == 0:
            return 
        
        remaining = "Estimating"
        rate = self.format_dict["rate"]
        if rate:
            remaining = (self.total - self.n) / rate
            remaining = humanfriendly.format_timespan(remaining)
        else:
            rate = 0

        if self.unit == "B":
            hrate = humanfriendly.format_size(rate) + "/S"
        else:
            hrate = humanfriendly.format_number(rate) + " it/S"

        msg = {
            "source": self.source,
            "desc": self.desc,
            "progress": self.n,
            "total": self.total,
            "position": self.position,
            "rate": hrate,
            "remaining": remaining
        }

        try:
            self._emit_update(msg)
        except socketio.exceptions.BadNamespaceError:
            pass

    def close(self):
        """
        Closes the progress bar and emits a final update with progress set to -1.
        """
        super().close()

        if len(self.sio_events) == 0:
            return 

        msg = {
            "source": self.source,
            "desc": self.desc,
            "progress": -1,
            "total": self.total,
            "position": self.position,
        }

        for sio, event, room in self.sio_events:
            self._emit_message(msg, sio, event, room)
