import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
// The app keeps the open chat in the address bar; each test starts from a clean URL.
afterEach(() => { cleanup(); window.history.replaceState(null, '', '/'); });
