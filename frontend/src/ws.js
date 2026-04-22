export function createWS(onMessage, onConnect, onDisconnect) {
    let socket = null;
    let delay = 1000;
    let dead = false;
    function connect() {
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        socket = new WebSocket(`${proto}://${location.host}/ws/voice`);
        socket.onopen = () => {
            delay = 1000;
            onConnect?.();
        };
        socket.onmessage = (e) => {
            try {
                onMessage(JSON.parse(e.data));
            }
            catch { /* skip */ }
        };
        socket.onclose = () => {
            onDisconnect?.();
            if (!dead) {
                setTimeout(() => {
                    delay = Math.min(delay * 2, 30000);
                    connect();
                }, delay);
            }
        };
    }
    connect();
    return {
        send(msg) {
            if (socket?.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify(msg));
            }
        },
        close() {
            dead = true;
            socket?.close();
        },
    };
}
