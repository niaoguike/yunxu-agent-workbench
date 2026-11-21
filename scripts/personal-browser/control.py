"""CDP is available only inside the browser container; protocol output is JSON."""
import json
import sys
import urllib.request
import websocket

request=json.load(sys.stdin)
with urllib.request.urlopen('http://127.0.0.1:9222/json/list',timeout=5) as response:
    pages=json.load(response)
page=next(p for p in pages if p.get('type')=='page')
ws=websocket.create_connection(page['webSocketDebuggerUrl'],origin='http://localhost',timeout=15)
seq=0

def call(method,params=None):
    global seq
    seq+=1
    ws.send(json.dumps({'id':seq,'method':method,'params':params or {}}))
    while True:
        result=json.loads(ws.recv())
        if result.get('id')==seq:
            if 'error' in result: raise RuntimeError('Browser command rejected')
            return result.get('result',{})

kind=request['kind']
if kind=='navigate': call('Page.navigate',{'url':request['url']})
elif kind=='click':
    x,y=float(request['x']),float(request['y'])
    if not 0<=x<=1280 or not 0<=y<=800: raise ValueError('Invalid coordinates')
    call('Input.dispatchMouseEvent',{'type':'mousePressed','x':x,'y':y,'button':'left','clickCount':1})
    call('Input.dispatchMouseEvent',{'type':'mouseReleased','x':x,'y':y,'button':'left','clickCount':1})
elif kind=='type': call('Input.insertText',{'text':request['text'][:16000]})
elif kind=='key':
    key=request['key']
    codes={'Enter':13,'Tab':9,'Backspace':8,'Escape':27,'ArrowDown':40,'ArrowUp':38}
    if key not in codes: raise ValueError('Invalid key')
    call('Input.dispatchKeyEvent',{'type':'keyDown','key':key,'windowsVirtualKeyCode':codes[key]})
    call('Input.dispatchKeyEvent',{'type':'keyUp','key':key,'windowsVirtualKeyCode':codes[key]})
result=call('Page.captureScreenshot',{'format':'png'})
print(json.dumps({'image':'data:image/png;base64,'+result['data'],'width':1280,'height':800}))
ws.close()
