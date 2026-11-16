import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';

function App() {
  const [status, setStatus] = useState('Checking backend availability…');

  useEffect(() => {
    const controller = new AbortController();
    fetch('/health', { signal: controller.signal })
      .then((response) => {
        setStatus(response.ok ? 'Backend is available.' : 'Backend is unavailable.');
      })
      .catch(() => {
        if (!controller.signal.aborted) setStatus('Backend is unavailable.');
      });
    return () => controller.abort();
  }, []);

  return (
    <main>
      <h1>TechScout</h1>
      <p role="status">{status}</p>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
