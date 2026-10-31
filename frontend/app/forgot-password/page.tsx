"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, KeyRound, Mail, RefreshCw } from "lucide-react";
import { Brand } from "../../components/SiteShell";
import { useAuth } from "../../components/AuthProvider";
import { authApi } from "../../lib/api";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const CODE_RE = /^\d{6}$/;
const SUCCESS_NOTICE = "Recovery code sent successfully. Please check your inbox.";
const FAILURE_ERROR = "Unable to send the recovery email right now. Please try again later.";

export default function ForgotPasswordPage() {
  const router = useRouter();
  const { user, loading, resetPassword } = useAuth();
  const [step, setStep] = useState<"request" | "reset">("request");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const initialEmail = new URLSearchParams(window.location.search).get("email");
    if (initialEmail) setEmail(initialEmail.trim());
  }, []);

  useEffect(() => {
    if (!loading && user) router.replace("/dashboard");
  }, [loading, user, router]);

  async function handleRequestCode(e: React.FormEvent) {
    e.preventDefault();
    setNotice("");
    const trimmedEmail = email.trim().toLowerCase();
    if (!EMAIL_RE.test(trimmedEmail)) {
      setError("Please enter a valid email address.");
      return;
    }
    setError("");
    setBusy(true);
    try {
      await authApi.forgotPassword(trimmedEmail);
      setEmail(trimmedEmail);
      setCode("");
      setStep("reset");
      setNotice(SUCCESS_NOTICE);
    } catch {
      setError(FAILURE_ERROR);
    } finally {
      setBusy(false);
    }
  }

  async function handleResendCode() {
    setNotice("");
    setError("");
    setBusy(true);
    try {
      await authApi.forgotPassword(email.trim().toLowerCase());
      setCode("");
      setNotice(SUCCESS_NOTICE);
    } catch {
      setError(FAILURE_ERROR);
    } finally {
      setBusy(false);
    }
  }

  async function handleResetPassword(e: React.FormEvent) {
    e.preventDefault();
    setNotice("");
    const cleanedCode = code.replace(/\s+/g, "");
    if (!CODE_RE.test(cleanedCode)) {
      setError("Please enter the 6-digit recovery code sent to your email.");
      return;
    }
    if (password.length < 12) {
      setError("Password must be at least 12 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setError("");
    setBusy(true);
    try {
      await resetPassword({
        email: email.trim().toLowerCase(),
        code: cleanedCode,
        password,
        confirm_password: confirm,
      });
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset password. Please check your recovery code.");
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <header className="auth-top">
        <Brand />
      </header>
      <main className="auth-card card">
        <p className="eyebrow">
          {step === "request" ? (
            <>
              <Mail size={13} /> PASSWORD RECOVERY
            </>
          ) : (
            <>
              <KeyRound size={13} /> VERIFY RECOVERY CODE
            </>
          )}
        </p>
        <h1>{step === "request" ? "Forgot your password?" : "Verify Recovery Code"}</h1>
        <p className="auth-lede">
          {step === "request"
            ? "Enter your registered email address to receive a 6-digit password recovery code."
            : "Check your email for a 6-digit recovery code sent to your registered email address. The code is valid for 15 minutes."}
        </p>

        {notice && (
          <p className="form-notice" role="status">
            {notice}
          </p>
        )}

        {step === "request" ? (
          <form onSubmit={handleRequestCode} noValidate>
            <div className="field">
              <label htmlFor="recovery-email">Registered email address</label>
              <input
                id="recovery-email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                maxLength={255}
                placeholder="you@example.com"
              />
            </div>
            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            <button className="button auth-submit" type="submit" disabled={busy}>
              {busy ? "Sending recovery code…" : "Send Recovery Code"} {!busy && <ArrowRight size={16} />}
            </button>
          </form>
        ) : (
          <form onSubmit={handleResetPassword} noValidate>
            <div className="field">
              <div className="field-label-row">
                <label htmlFor="recovery-code">6-digit recovery code</label>
                <button
                  type="button"
                  className="inline-link-btn"
                  onClick={handleResendCode}
                  disabled={busy}
                >
                  <RefreshCw size={12} /> Resend Code
                </button>
              </div>
              <input
                id="recovery-code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                className="recovery-code-input"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/[^\d]/g, "").slice(0, 6))}
                maxLength={6}
                placeholder="000000"
              />
            </div>
            <div className="field">
              <label htmlFor="new-password">New Password</label>
              <input
                id="new-password"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                maxLength={128}
              />
              <small>At least 12 characters.</small>
            </div>
            <div className="field">
              <label htmlFor="confirm-new-password">Confirm New Password</label>
              <input
                id="confirm-new-password"
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                maxLength={128}
              />
            </div>
            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            <button className="button auth-submit" type="submit" disabled={busy}>
              {busy ? "Resetting password…" : "Reset Password"} {!busy && <ArrowRight size={16} />}
            </button>
            <div className="recovery-secondary-actions">
              <button
                type="button"
                className="inline-link-btn"
                onClick={() => {
                  setStep("request");
                  setError("");
                  setNotice("");
                  setCode("");
                }}
                disabled={busy}
              >
                Use a Different Email Address
              </button>
            </div>
          </form>
        )}

        <p className="auth-alt">
          <Link className="text-link" href="/login">
            <ArrowLeft size={14} /> Back to Sign In
          </Link>
        </p>
      </main>
    </div>
  );
}
