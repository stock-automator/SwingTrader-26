import type {
  AlertChannelsConfig,
  AlertChannelsConfigResponse,
  AlertChannelName,
  AlertTestResponse,
  AlpacaAccount,
  BacktestRequest,
  BacktestResponse,
  CloseAllPositionsResponse,
  DataSyncResponse,
  DataSyncStatusResponse,
  ExecutionOrderRequest,
  ExecutionOrderResponse,
  HealthResponse,
  JournalDecay,
  JournalSummary,
  JournalTradesResponse,
  MaeMfeDistributionResponse,
  OrderTicketRequest,
  OrderTicketsResponse,
  PortfolioHistory,
  Position,
  ScreenerResponse,
  SignalMatrixResponse,
  Strategy,
} from "../types";

export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export const WS_BASE_URL: string = API_BASE_URL.replace(/^http/, "ws");

class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail ?? detail;
    } catch {
      // ignore parse errors, fall back to statusText
    }
    throw new ApiError(
      typeof detail === "string" ? detail : JSON.stringify(detail),
      res.status,
    );
  }
  return (await res.json()) as T;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/v1/health");
}

export interface LiveScreenerParams {
  strategy: Strategy;
  account_equity?: number;
  risk_per_trade_pct?: number;
  tickers?: string[];
  earnings_blackout?: boolean;
}

function screenerSearchParams(params: LiveScreenerParams): URLSearchParams {
  const search = new URLSearchParams();
  search.set("strategy", params.strategy);
  if (params.account_equity !== undefined) {
    search.set("account_equity", String(params.account_equity));
  }
  if (params.risk_per_trade_pct !== undefined) {
    search.set("risk_per_trade_pct", String(params.risk_per_trade_pct));
  }
  if (params.tickers && params.tickers.length > 0) {
    search.set("tickers", params.tickers.join(","));
  }
  if (params.earnings_blackout) {
    search.set("earnings_blackout", "true");
  }
  return search;
}

export function getLiveScreener(
  params: LiveScreenerParams,
): Promise<ScreenerResponse> {
  const search = screenerSearchParams(params);
  return request<ScreenerResponse>(
    `/api/v1/screener/live?${search.toString()}`,
  );
}

export function screenerWebSocketUrl(params: LiveScreenerParams): string {
  const search = screenerSearchParams(params);
  return `${WS_BASE_URL}/ws/screener?${search.toString()}`;
}

export function runBacktest(body: BacktestRequest): Promise<BacktestResponse> {
  return request<BacktestResponse>("/api/v1/backtest", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getOrderTickets(
  body: OrderTicketRequest,
): Promise<OrderTicketsResponse> {
  return request<OrderTicketsResponse>("/api/v1/order-ticket", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getDataSyncStatus(): Promise<DataSyncStatusResponse> {
  return request<DataSyncStatusResponse>("/api/v1/data/sync/status");
}

export function startDataSync(tickers?: string[]): Promise<DataSyncResponse> {
  return request<DataSyncResponse>("/api/v1/data/sync", {
    method: "POST",
    body: JSON.stringify({ tickers: tickers ?? null }),
  });
}

export interface SignalMatrixParams {
  strategies?: string[];
  tickers?: string[];
  account_equity?: number;
  risk_per_trade_pct?: number;
  earnings_blackout?: boolean;
}

export function getSignalMatrix(
  params: SignalMatrixParams = {},
): Promise<SignalMatrixResponse> {
  const search = new URLSearchParams();
  if (params.strategies && params.strategies.length > 0) {
    search.set("strategies", params.strategies.join(","));
  }
  if (params.tickers && params.tickers.length > 0) {
    search.set("tickers", params.tickers.join(","));
  }
  if (params.account_equity !== undefined) {
    search.set("account_equity", String(params.account_equity));
  }
  if (params.risk_per_trade_pct !== undefined) {
    search.set("risk_per_trade_pct", String(params.risk_per_trade_pct));
  }
  if (params.earnings_blackout) {
    search.set("earnings_blackout", "true");
  }
  const qs = search.toString();
  return request<SignalMatrixResponse>(
    `/api/v1/signals/live-today${qs ? `?${qs}` : ""}`,
  );
}

export function submitOrder(
  body: ExecutionOrderRequest,
): Promise<ExecutionOrderResponse> {
  return request<ExecutionOrderResponse>("/api/v1/execution/orders", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ---- Execution: account, positions, portfolio history ----

export function getAccount(): Promise<AlpacaAccount> {
  return request<AlpacaAccount>("/api/v1/execution/account");
}

export function getPositions(): Promise<{ positions: Position[] }> {
  return request<{ positions: Position[] }>("/api/v1/execution/positions");
}

export function getPortfolioHistory(
  period = "1M",
  timeframe = "1D",
): Promise<PortfolioHistory> {
  const search = new URLSearchParams({ period, timeframe });
  return request<PortfolioHistory>(
    `/api/v1/execution/portfolio-history?${search.toString()}`,
  );
}

export function closeAllPositions(): Promise<CloseAllPositionsResponse> {
  return request<CloseAllPositionsResponse>("/api/v1/execution/close-all", {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  });
}

// ---- Alerts ----

export function getAlertConfig(): Promise<AlertChannelsConfigResponse> {
  return request<AlertChannelsConfigResponse>("/api/v1/alerts/config");
}

export function putAlertConfig(
  body: AlertChannelsConfig,
): Promise<AlertChannelsConfigResponse> {
  return request<AlertChannelsConfigResponse>("/api/v1/alerts/config", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function sendTestAlert(
  channel: AlertChannelName,
): Promise<AlertTestResponse> {
  return request<AlertTestResponse>("/api/v1/alerts/test", {
    method: "POST",
    body: JSON.stringify({ channel }),
  });
}

// ---- Trade journal ----

export function getJournalSummary(): Promise<JournalSummary> {
  return request<JournalSummary>("/api/v1/journal/summary");
}

export function getJournalDecay(
  windows = "30,60,90",
): Promise<JournalDecay> {
  return request<JournalDecay>(
    `/api/v1/journal/decay?windows=${encodeURIComponent(windows)}`,
  );
}

export function getJournalTrades(): Promise<JournalTradesResponse> {
  return request<JournalTradesResponse>("/api/v1/journal/trades");
}

export function getMaeMfeDistribution(): Promise<MaeMfeDistributionResponse> {
  return request<MaeMfeDistributionResponse>(
    "/api/v1/journal/mae-mfe-distribution",
  );
}

export { ApiError };
