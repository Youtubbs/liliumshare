from pynput.keyboard import Controller as KController, Key
from pynput.mouse import Controller as MController, Button

k = KController()
m = MController()

def apply_event(evt, permissions):
    # evt example:
    # {"type":"mouse","action":"move","dx":10,"dy":-5}
    # {"type":"mouse","action":"click","button":"left","down":true}
    # {"type":"keyboard","action":"type","text":"hello"}
    # {"type":"keyboard","action":"key","key":"alt","down":true}
    # Immersion (alt-tab etc.) is allowed only if permissions.get("immersion")
    t = evt.get("type")
    if t == "mouse" and permissions.get("mouse"):
        act = evt.get("action")
        if act == "move":
            dx, dy = evt.get("dx",0), evt.get("dy",0)
            x, y = m.position
            m.position = (x+dx, y+dy)
        elif act == "click":
            btn = Button.left if evt.get("button")=="left" else Button.right
            down = evt.get("down")
            (m.press if down else m.release)(btn)
        elif act == "scroll":
            m.scroll(evt.get("dx",0), evt.get("dy",0))
    elif t == "keyboard" and permissions.get("keyboard"):
        act = evt.get("action")
        if act == "type":
            text = evt.get("text","")
            k.type(text)
        elif act == "key":
            keyname = evt.get("key")
            down = evt.get("down")
            # Limit immersion keys unless allowed
            if keyname in ("alt","cmd","win","tab","esc") and not permissions.get("immersion"):
                return
            mapping = {
                "alt": Key.alt,
                "tab": Key.tab,
                "esc": Key.esc,
                "enter": Key.enter,
                "shift": Key.shift,
                "ctrl": Key.ctrl,
                "cmd": Key.cmd,
                "win": Key.cmd,
            }
            key = mapping.get(keyname)
            if key:
                (k.press if down else k.release)(key)
            else:
                # Single-char fallback
                if down:
                    k.press(keyname)
                else:
                    k.release(keyname)
    # controller input: TODO (stub)
