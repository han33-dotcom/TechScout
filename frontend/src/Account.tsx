import { useRef, useState, type FormEvent } from 'react';
import { explain, type AccountSummary, type BuyerApi } from './api';

export type AccountTransport = Pick<BuyerApi, 'register' | 'signIn' | 'signOut'>;

/** Registration, sign-in and sign-out. Passwords live only in this form until submitted. */
export default function Account({ api, account, purpose, onSignedIn, onSignedOut, onBack }: {
  api: AccountTransport; account: AccountSummary | null | undefined; purpose?: string;
  onSignedIn: (account: AccountSummary) => void; onSignedOut: () => void; onBack: () => void;
}) {
  const [mode, setMode] = useState<'sign-in' | 'register'>('sign-in');
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [confirmSignOut, setConfirmSignOut] = useState(false);
  const locked = useRef(false);
  async function perform(action: () => Promise<void>, fallback: string) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError('');
    try { await action(); }
    catch (e) { setError(`${explain(e, fallback)} Nothing was retried automatically.`); }
    finally { locked.current = false; setBusy(false); }
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    if (mode === 'register' && password !== confirm) { setError('The two passwords do not match.'); return; }
    void perform(async () => {
      const result = mode === 'register' ? await api.register(username, password) : await api.signIn(username, password);
      setPassword(''); setConfirm('');
      if (result.account) onSignedIn(result.account);
    }, mode === 'register' ? 'Could not create the account.' : 'Could not sign in.');
  }
  return <section className="panel account" aria-label="TechScout account">
    <div className="section-heading"><div><span className="eyebrow">TECHSCOUT ACCOUNT</span>
      <h2>{account ? `Signed in as ${account.username}` : mode === 'register' ? 'Create your TechScout account' : 'Sign in to TechScout'}</h2></div>
      <button type="button" disabled={busy} onClick={onBack}>Back to your search</button></div>
    {purpose && !account && <p className="notice">{purpose}</p>}
    {account ? <>
      <p>Your saved locations stay private to your account. This browser's searches belong to this session.</p>
      {!confirmSignOut ? <button type="button" disabled={busy} onClick={() => setConfirmSignOut(true)}>Sign out</button>
        : <div className="delete-confirm"><p>Signing out ends this browser session and deletes its searches. Your account and saved locations stay.</p>
          <button type="button" disabled={busy} onClick={() => void perform(async () => { await api.signOut(); setConfirmSignOut(false); onSignedOut(); }, 'Could not sign out.')}>Sign out and clear searches</button>
          <button type="button" disabled={busy} onClick={() => setConfirmSignOut(false)}>Stay signed in</button></div>}
    </> : <form className="account-form" onSubmit={submit}>
      <div className="category-row" role="group" aria-label="Account action">
        <button type="button" aria-pressed={mode === 'sign-in'} onClick={() => { setMode('sign-in'); setError(''); }}>Sign in</button>
        <button type="button" aria-pressed={mode === 'register'} onClick={() => { setMode('register'); setError(''); }}>Create account</button>
      </div>
      <label>Username<input name="username" autoComplete="username" required maxLength={64} value={username} onChange={e => setUsername(e.target.value)} /></label>
      {mode === 'register' && <small className="muted">3–32 lowercase letters, digits, dots, dashes or underscores. No email or phone number is needed.</small>}
      <label>Password<input name="password" type="password" required maxLength={128} autoComplete={mode === 'register' ? 'new-password' : 'current-password'} value={password} onChange={e => setPassword(e.target.value)} /></label>
      {mode === 'register' && <><small className="muted">At least 10 characters, not containing your username.</small>
        <label>Confirm password<input name="confirm" type="password" required maxLength={128} autoComplete="new-password" value={confirm} onChange={e => setConfirm(e.target.value)} /></label></>}
      <button className="primary" disabled={busy}>{busy ? 'Please wait…' : mode === 'register' ? 'Create account' : 'Sign in'}</button>
    </form>}
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}
