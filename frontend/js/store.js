// 纯原生的状态中心 (Simple State Management)
class Store {
  constructor() {
    this.state = {
      config: {
        theme: localStorage.getItem('chat-theme') || 'dark',
        apiBaseUrl: '',
        bubbleMode: localStorage.getItem('chat-bubble') === '1',
        ttsAuto: localStorage.getItem('tts_auto') === '1',
      },
      serverConfig: null,
      activeSessionId: null,
      activeCharacter: null,
      sessions: [],
      contacts: [],
      messages: {},
      mode: 'api',
    };
    this.listeners = new Set();
  }

  getState() { return this.state; }

  setState(partialState) {
    this.state = { ...this.state, ...partialState };
    this.notify();
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  notify() {
    for (const listener of this.listeners) {
      try { listener(this.state); } catch (e) { console.error(e); }
    }
  }
}

export const store = new Store();
