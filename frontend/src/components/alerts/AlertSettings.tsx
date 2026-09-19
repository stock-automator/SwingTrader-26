import { useEffect, useState } from "react";
import { Bell, CheckCircle2, Save, Send, XCircle } from "lucide-react";
import { toast } from "sonner";
import { ApiError, getAlertConfig, putAlertConfig, sendTestAlert } from "../../lib/api";
import type { AlertChannelName, AlertChannelsConfigResponse } from "../../types";

type FormState = {
  telegram_bot_token: string;
  telegram_chat_id: string;
  discord_webhook_url: string;
  generic_webhook_url: string;
};

const EMPTY_FORM: FormState = {
  telegram_bot_token: "",
  telegram_chat_id: "",
  discord_webhook_url: "",
  generic_webhook_url: "",
};

function toForm(config: AlertChannelsConfigResponse): FormState {
  return {
    telegram_bot_token: config.telegram_bot_token ?? "",
    telegram_chat_id: config.telegram_chat_id ?? "",
    discord_webhook_url: config.discord_webhook_url ?? "",
    generic_webhook_url: config.generic_webhook_url ?? "",
  };
}

function ConfiguredBadge({ configured }: { configured: boolean }) {
  return configured ? (
    <span className="flex items-center gap-1 text-xs text-long">
      <CheckCircle2 size={13} /> Connected
    </span>
  ) : (
    <span className="flex items-center gap-1 text-xs text-text-faint">
      <XCircle size={13} /> Not configured
    </span>
  );
}

function TestAlertButton({
  channel,
  disabled,
  testId,
}: {
  channel: AlertChannelName;
  disabled: boolean;
  testId: string;
}) {
  const [sending, setSending] = useState(false);

  async function handleTest() {
    setSending(true);
    try {
      await sendTestAlert(channel);
      toast.success(`Test alert sent to ${channel}`);
    } catch (err) {
      // An ApiError's message is already the backend's descriptive detail
      // string (e.g. "Test alert failed: connection refused") - prefixing
      // it again would double up; a network-level failure has no such
      // context, so it still gets one.
      const message =
        err instanceof ApiError
          ? err.message
          : `Test alert failed: ${err instanceof Error ? err.message : String(err)}`;
      toast.error(message);
    } finally {
      setSending(false);
    }
  }

  return (
    <button
      onClick={handleTest}
      disabled={disabled || sending}
      data-testid={testId}
      className="flex items-center gap-1.5 rounded border border-border bg-panel-alt px-2.5 py-1 text-xs text-text hover:border-accent disabled:cursor-not-allowed disabled:opacity-40"
    >
      <Send size={12} />
      {sending ? "Sending…" : "Send Test Alert"}
    </button>
  );
}

interface FieldProps {
  label: string;
  value: string;
  placeholder: string;
  testId: string;
  type?: string;
  onChange: (value: string) => void;
}

function Field({ label, value, placeholder, testId, type = "text", onChange }: FieldProps) {
  return (
    <label className="flex flex-col gap-1 text-xs text-text-dim">
      {label}
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
        className="rounded border border-border bg-panel-alt px-2 py-1.5 text-sm text-text placeholder:text-text-faint"
      />
    </label>
  );
}

export function AlertSettings() {
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [config, setConfig] = useState<AlertChannelsConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAlertConfig()
      .then((res) => {
        setConfig(res);
        setForm(toForm(res));
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

  async function handleSave() {
    setSaving(true);
    try {
      const res = await putAlertConfig({
        telegram_bot_token: form.telegram_bot_token || null,
        telegram_chat_id: form.telegram_chat_id || null,
        discord_webhook_url: form.discord_webhook_url || null,
        generic_webhook_url: form.generic_webhook_url || null,
      });
      setConfig(res);
      setForm(toForm(res));
      toast.success("Alert channel settings saved");
    } catch (err) {
      toast.error(
        `Failed to save alert settings: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="text-sm text-text-dim">Loading…</div>;
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-bold tracking-widest text-text">
          <Bell size={16} /> ALERT CHANNELS
        </h2>
        <button
          onClick={handleSave}
          disabled={saving}
          data-testid="alert-settings-save"
          className="flex items-center gap-1.5 rounded border border-accent/60 bg-accent/10 px-3 py-1.5 text-sm font-semibold text-accent hover:bg-accent/20 disabled:opacity-50"
        >
          <Save size={14} />
          {saving ? "Saving…" : "Save Channel Settings"}
        </button>
      </div>

      {error && (
        <div className="rounded border border-short-dim bg-short-dim/20 px-3 py-2 text-sm text-short">
          {error}
        </div>
      )}

      <p className="text-xs text-text-faint">
        Configure alert channels here — no backend files or restarts
        required. Settings are saved server-side and take effect on the
        next dispatch.
      </p>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <div
          className="flex flex-col gap-3 rounded border border-border bg-panel p-4"
          data-testid="channel-card-telegram"
        >
          <div className="flex items-center justify-between">
            <div className="text-sm font-semibold text-text">Telegram</div>
            <ConfiguredBadge configured={config?.telegram_configured ?? false} />
          </div>
          <Field
            label="Bot Token"
            value={form.telegram_bot_token}
            placeholder="123456:ABC-DEF..."
            testId="telegram-bot-token-input"
            onChange={(v) => setForm((f) => ({ ...f, telegram_bot_token: v }))}
          />
          <Field
            label="Chat ID"
            value={form.telegram_chat_id}
            placeholder="-1001234567890"
            testId="telegram-chat-id-input"
            onChange={(v) => setForm((f) => ({ ...f, telegram_chat_id: v }))}
          />
          <TestAlertButton
            channel="telegram"
            disabled={!config?.telegram_configured}
            testId="telegram-test-button"
          />
        </div>

        <div
          className="flex flex-col gap-3 rounded border border-border bg-panel p-4"
          data-testid="channel-card-discord"
        >
          <div className="flex items-center justify-between">
            <div className="text-sm font-semibold text-text">Discord</div>
            <ConfiguredBadge configured={config?.discord_configured ?? false} />
          </div>
          <Field
            label="Webhook URL"
            value={form.discord_webhook_url}
            placeholder="https://discord.com/api/webhooks/…"
            testId="discord-webhook-input"
            onChange={(v) => setForm((f) => ({ ...f, discord_webhook_url: v }))}
          />
          <TestAlertButton
            channel="discord"
            disabled={!config?.discord_configured}
            testId="discord-test-button"
          />
        </div>

        <div
          className="flex flex-col gap-3 rounded border border-border bg-panel p-4"
          data-testid="channel-card-webhook"
        >
          <div className="flex items-center justify-between">
            <div className="text-sm font-semibold text-text">
              Custom JSON Webhook
            </div>
            <ConfiguredBadge configured={config?.webhook_configured ?? false} />
          </div>
          <Field
            label="Endpoint URL"
            value={form.generic_webhook_url}
            placeholder="https://example.com/hook"
            testId="generic-webhook-input"
            onChange={(v) => setForm((f) => ({ ...f, generic_webhook_url: v }))}
          />
          <TestAlertButton
            channel="webhook"
            disabled={!config?.webhook_configured}
            testId="webhook-test-button"
          />
        </div>
      </div>
    </div>
  );
}
