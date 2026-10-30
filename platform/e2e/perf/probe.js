// In-page clocks only. MessageChannel runs after the rAF rendering opportunity;
// this is a presentation proxy, not proof of photons reaching a physical display.
window.perfProbe = {keys: [], frames: [], tasks: [], mutations: [], deltas: [], visible: [], pending: [], text: "", active: false};
const p = window.perfProbe;
const afterFrame = callback => requestAnimationFrame(() => {
  const channel = new MessageChannel();
  channel.port1.onmessage = () => { callback(performance.now()); channel.port1.close(); channel.port2.close(); };
  channel.port2.postMessage(0);
});
document.addEventListener('keydown', event => {
  if (event.target.matches('[role="textbox"][aria-label="消息"]') && event.key.length === 1) {
    const start = performance.now();
    afterFrame(now => p.keys.push(now - start));
  }
}, true);
new PerformanceObserver(list => {
  for (const entry of list.getEntries()) {
    if (p.active && entry.startTime >= p.start && entry.duration > 50) p.tasks.push(entry.duration);
  }
}).observe({type: 'longtask', buffered: true});
p.begin = () => {
  p.armed = true;
  p.previous = [...document.querySelectorAll('.turn')];
};
p.activate = () => {
  p.active = true; p.start = performance.now(); p.last = null;
  // Freeze the set before submitting: the newly appended streaming turn is excluded.
  // Each record keeps its time, so the test can tell the stream itself from the run settling after the last delta.
  p.observer = new MutationObserver(records => {
    if (!p.active) return;
    const now = performance.now();
    // A record on the .turn itself is bounded rendering swapping an off-screen turn for its placeholder (or back);
    // anything deeper is the turn's content changing.
    for (const r of records) p.mutations.push({t: now, mount: !!r.target.classList?.contains('turn'), what: `${r.type}:${r.attributeName || ''}:${r.target.className || r.target.nodeName}`.slice(0, 80)});
  });
  for (const turn of p.previous) p.observer.observe(turn, {subtree:true, childList:true, characterData:true, attributes:true});
  const tick = now => {
    if (!p.active) return;
    if (p.last !== null) p.frames.push(now - p.last);
    p.last = now;
    const body = [...document.querySelectorAll('.turn')].at(-1)?.querySelector('.assistant-body');
    const text = body?.textContent || '';
    const ready = p.pending.filter(item => text.includes(item.text));
    p.pending = p.pending.filter(item => !text.includes(item.text));
    if (ready.length) {
      const channel = new MessageChannel();
      channel.port1.onmessage = () => {
        for (const item of ready) p.visible.push(performance.now() - item.time);
        channel.port1.close(); channel.port2.close();
      };
      channel.port2.postMessage(0);
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
};
p.end = () => { p.active = false; p.observer.disconnect(); };
// Observe the exact stream reader used by the app, without teeing/buffering a second stream.
const originalFetch = window.fetch;
window.fetch = async (...args) => {
  const response = await originalFetch(...args);
  if (/\/runs\/[^/]+\/events\?/.test(String(args[0])) && response.body) {
    const getReader = response.body.getReader.bind(response.body);
    response.body.getReader = (...readerArgs) => {
      const reader = getReader(...readerArgs), read = reader.read.bind(reader);
      const decoder = new TextDecoder(); let pending = '';
      reader.read = async (...readArgs) => {
        const result = await read(...readArgs);
        if (!result.done && p.armed) {
          pending += decoder.decode(result.value, {stream:true});
          let boundary;
          while ((boundary = pending.indexOf('\n\n')) >= 0) {
            const event = pending.slice(0, boundary); pending = pending.slice(boundary + 2);
            if (event.includes('event: agent.delta')) {
              if (!p.active) p.activate();
              const data = JSON.parse(event.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n'));
              const time = performance.now();
              p.deltas.push(time); p.text += data.text;
              p.pending.push({text:p.text, time});
            }
          }
        }
        return result;
      };
      return reader;
    };
  }
  return response;
};
