"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { SiteFooter, SiteHeader } from "@/components/Chrome";
import { api, setAccessToken } from "@/lib/api";

export default function SignInPage() {
  return (
    <Suspense fallback={null}>
      <SignInForm />
    </Suspense>
  );
}

function SignInForm() {
  const params = useSearchParams();
  const router = useRouter();
  const [mode, setMode] = useState<"signin" | "register">(
    params.get("mode") === "register" ? "register" : "signin",
  );
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [needsMfa, setNeedsMfa] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setMessage(null);
    setBusy(true);

    try {
      if (mode === "register") {
        const result = await api.register(email, password);
        setMessage(result.message);
        setMode("signin");
      } else {
        const result = await api.login(email, password, totpCode || undefined);
        if (result.mfa_required) {
          // No token is issued yet -- the server withholds it until the second
          // factor is supplied, so there is nothing to store at this point.
          setNeedsMfa(true);
          setMessage("Enter the code from your authenticator app.");
        } else {
          setAccessToken(result.access_token);
          router.push("/dashboard");
        }
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <SiteHeader />
      <main id="main" className="shell" style={{ paddingBlock: "3rem", maxWidth: "26rem" }}>
        <h1 style={{ fontSize: "1.75rem" }}>
          {mode === "register" ? "Create your account" : "Sign in"}
        </h1>

        {message && (
          <div className="notice notice--info">
            <span className="notice__icon" aria-hidden="true">ℹ</span>
            <div className="notice__body">
              <p className="small" style={{ margin: 0 }}>{message}</p>
            </div>
          </div>
        )}

        {error && (
          <div className="notice notice--danger" role="alert">
            <span className="notice__icon" aria-hidden="true">⚠</span>
            <div className="notice__body">
              <p className="small" style={{ margin: 0 }}>{error}</p>
            </div>
          </div>
        )}

        <form onSubmit={submit} noValidate>
          <div className="field">
            <label className="field__label" htmlFor="email">Email address</label>
            <input
              id="email"
              className="input"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>

          <div className="field">
            <label className="field__label" htmlFor="password">Password</label>
            {mode === "register" && (
              <span className="field__hint">
                At least 12 characters. A short phrase you will remember beats a
                short scramble you will not.
              </span>
            )}
            <input
              id="password"
              className="input"
              type="password"
              autoComplete={mode === "register" ? "new-password" : "current-password"}
              required
              minLength={mode === "register" ? 12 : undefined}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>

          {needsMfa && (
            <div className="field">
              <label className="field__label" htmlFor="totp">Authentication code</label>
              <input
                id="totp"
                className="input"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                value={totpCode}
                onChange={(event) => setTotpCode(event.target.value)}
                style={{ maxWidth: "10rem", letterSpacing: "0.2em" }}
              />
            </div>
          )}

          <button className="btn btn--primary btn--block" type="submit" disabled={busy}>
            {busy ? "Please wait…" : mode === "register" ? "Create account" : "Sign in"}
          </button>
        </form>

        <p className="small" style={{ marginTop: "1.5rem" }}>
          {mode === "register" ? "Already have an account? " : "New to OlbosTax? "}
          <button
            type="button"
            className="btn btn--ghost"
            style={{ padding: 0, minHeight: 0, color: "var(--teal-700)", fontWeight: 640 }}
            onClick={() => {
              setMode(mode === "register" ? "signin" : "register");
              setError(null);
            }}
          >
            {mode === "register" ? "Sign in" : "Create one"}
          </button>
        </p>
      </main>
      <SiteFooter />
    </>
  );
}
