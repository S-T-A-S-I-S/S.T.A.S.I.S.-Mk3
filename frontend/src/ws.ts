type MsgHandler = (msg: Record<string, unknown>) => void;

export interface WsClient {
  send(msg: object): void;
  close(): void;
}

export function createWS(
  onMessage: MsgHandler,
  onConnect?: () => void,
  onDisconnect?: () => void,
): WsClient {
  let socket: WebSocket | null = null;
  let delay = 1_000;
  let dead = false;

  function connect(): void {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    socket = new WebSocket(`${proto}://${location.host}/ws/voice`);

    socket.onopen = () => {
      delay = 1_000;
      onConnect?.();
    };

    socket.onmessage = (e: MessageEvent) => {
      try { onMessage(JSON.parse(e.data as string)); } catch { /* skip */ }
    };

    socket.onclose = () => {
      onDisconnect?.();
      if (!dead) {
        setTimeout(() => {
          delay = Math.min(delay * 2, 30_000);
          connect();
        }, delay);
      }
    };
  }

  connect();

  return {
    send(msg: object): void {
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(msg));
      }
    },
    close(): void {
      dead = true;
      socket?.close();
    },
  };
}
