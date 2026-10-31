"""Bounded own-process X11 test gestures; never accepts a target PID/window from a model.

Only fixture OS integration. Geometry, widgets and production event dispatch stay
unchanged. WM_DELETE_WINDOW is an actual window-manager close request, not a call
of the assistant's shutdown hook.
"""
import ctypes as C
import ctypes.util
import os

UL=C.c_ulong
class Data(C.Union):
    _fields_=[('b',C.c_char*20),('s',C.c_short*10),('l',C.c_long*5)]
class Client(C.Structure):
    _fields_=[('type',C.c_int),('serial',UL),('send_event',C.c_int),('display',C.c_void_p),
              ('window',UL),('message_type',UL),('format',C.c_int),('data',Data)]
class Event(C.Union):
    _fields_=[('client',Client),('pad',C.c_long*24)]

class OwnedWindow:
    def __init__(self,width,height):
        library=ctypes.util.find_library('X11')
        if not library:raise RuntimeError('X11 library unavailable; no OS gesture sent')
        self.x=C.CDLL(library)
        specs={
            'XOpenDisplay':([C.c_char_p],C.c_void_p),'XDefaultRootWindow':([C.c_void_p],UL),
            'XInternAtom':([C.c_void_p,C.c_char_p,C.c_int],UL),
            'XQueryTree':([C.c_void_p,UL,C.POINTER(UL),C.POINTER(UL),C.POINTER(C.POINTER(UL)),C.POINTER(C.c_uint)],C.c_int),
            'XGetWindowProperty':([C.c_void_p,UL,UL,C.c_long,C.c_long,C.c_int,UL,C.POINTER(UL),C.POINTER(C.c_int),C.POINTER(UL),C.POINTER(UL),C.POINTER(C.POINTER(C.c_ubyte))],C.c_int),
            'XGetGeometry':([C.c_void_p,UL,C.POINTER(UL),C.POINTER(C.c_int),C.POINTER(C.c_int),C.POINTER(C.c_uint),C.POINTER(C.c_uint),C.POINTER(C.c_uint),C.POINTER(C.c_uint)],C.c_int),
            'XFree':([C.c_void_p],C.c_int),'XFlush':([C.c_void_p],C.c_int),
            'XResizeWindow':([C.c_void_p,UL,C.c_uint,C.c_uint],C.c_int),
            'XSendEvent':([C.c_void_p,UL,C.c_int,C.c_long,C.POINTER(Event)],C.c_int),
            'XCloseDisplay':([C.c_void_p],C.c_int),
        }
        for name,(args,result) in specs.items():
            fn=getattr(self.x,name);fn.argtypes=args;fn.restype=result
        self.display=self.x.XOpenDisplay(None)
        if not self.display:raise RuntimeError('Own X11 display unavailable')
        self.pid=os.getpid();self.window=None
        queue=[(self.x.XDefaultRootWindow(self.display),0)];matches=[];seen=0
        while queue:
            wid,depth=queue.pop();seen+=1
            if seen>4096:raise RuntimeError('Window discovery bound exceeded')
            if self.property(wid,'_NET_WM_PID')==[self.pid]:
                size=self.geometry(wid)
                if abs(size[0]-width)<=2 and abs(size[1]-height)<=2:matches.append(wid)
            if depth>=3:continue
            root,parent=UL(),UL();children=C.POINTER(UL)();count=C.c_uint()
            if self.x.XQueryTree(self.display,wid,C.byref(root),C.byref(parent),C.byref(children),C.byref(count)):
                try:
                    if count.value>4096:raise RuntimeError('Window child bound exceeded')
                    queue.extend((children[i],depth+1) for i in range(count.value))
                finally:
                    if children:self.x.XFree(children)
        if len(matches)!=1:raise RuntimeError('No unique own-process window with expected client dimensions')
        self.window=matches[0]
    def atom(self,name):return self.x.XInternAtom(self.display,name.encode(),1)
    def property(self,wid,name):
        atom=self.atom(name)
        if not atom:return []
        typ,fmt,count,remaining=UL(),C.c_int(),UL(),UL();data=C.POINTER(C.c_ubyte)()
        status=self.x.XGetWindowProperty(self.display,wid,atom,0,32,False,0,C.byref(typ),C.byref(fmt),C.byref(count),C.byref(remaining),C.byref(data))
        try:
            if status or fmt.value!=32 or remaining.value or count.value>32:return []
            return list(C.cast(data,C.POINTER(UL))[:count.value]) if data else []
        finally:
            if data:self.x.XFree(data)
    def geometry(self,wid=None):
        root=UL();x,y=C.c_int(),C.c_int();w,h,border,depth=C.c_uint(),C.c_uint(),C.c_uint(),C.c_uint()
        if not self.x.XGetGeometry(self.display,wid or self.window,C.byref(root),C.byref(x),C.byref(y),C.byref(w),C.byref(h),C.byref(border),C.byref(depth)):
            raise RuntimeError('Own window geometry unavailable')
        return w.value,h.value
    def verify(self):
        if self.property(self.window,'_NET_WM_PID')!=[self.pid] or self.pid!=os.getpid():
            raise RuntimeError('Window ownership changed; no gesture sent')
    def resize(self,width,height):
        self.verify()
        if not (640<=width<=2000 and 400<=height<=1200):raise ValueError('Test resize bound')
        self.x.XResizeWindow(self.display,self.window,width,height);self.x.XFlush(self.display)
    def request_close(self):
        self.verify();delete=self.atom('WM_DELETE_WINDOW');protocols=self.atom('WM_PROTOCOLS')
        if not delete or delete not in self.property(self.window,'WM_PROTOCOLS'):
            raise RuntimeError('Own window does not advertise WM_DELETE_WINDOW')
        event=Event();event.client.type=33;event.client.display=self.display;event.client.window=self.window
        event.client.message_type=protocols;event.client.format=32;event.client.data.l[0]=delete;event.client.data.l[1]=0
        if not self.x.XSendEvent(self.display,self.window,False,0,C.byref(event)):
            raise RuntimeError('OS close request could not be queued')
        self.x.XFlush(self.display)
    def dispose(self):
        if self.display:self.x.XCloseDisplay(self.display);self.display=None
