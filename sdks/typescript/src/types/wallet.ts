/**
 * Wallet-related types for CDP wallet operations.
 */

export interface SendPaymentRequest {
  to_address: string;
  amount: string;
  asset?: string;
}

export interface SendPaymentResponse {
  tx_hash: string;
  status: string;
  amount: string;
  asset: string;
  to: string;
}

export interface TradeTokensRequest {
  from_asset: string;
  to_asset: string;
  amount: string;
}

export interface TradeTokensResponse {
  trade_id: string;
  from_asset: string;
  to_asset: string;
  amount: string;
  status: string;
}

export interface StakeTokensRequest {
  amount: string;
  asset?: string;
}

export interface StakeTokensResponse {
  stake_id: string;
  amount: string;
  asset: string;
  status: string;
  validator_address?: string;
}

export interface WalletBalance {
  asset: string;
  balance: string;
  usd_value?: string;
}

export interface WalletInfo {
  network_id: string;
  wallet_id: string;
  address: string;
  balances: WalletBalance[];
}
