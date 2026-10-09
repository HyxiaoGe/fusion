export type McpTransport = 'streamable_http';

export type McpAuthType = 'none' | 'bearer' | 'header' | 'query';

export type McpHealthStatus = 'unknown' | 'healthy' | 'unhealthy' | 'disabled';

export interface McpDiscoveredTool {
  name: string;
  description?: string | null;
  input_schema?: Record<string, unknown>;
}

export interface McpServerPayload {
  name: string;
  provider: string;
  endpoint_url: string;
  transport: McpTransport;
  auth_type: McpAuthType;
  auth_name?: string | null;
  credential_ref?: string | null;
  allowed_tools: string[];
}

export interface McpServer extends McpServerPayload {
  id: string;
  is_enabled: boolean;
  health_status: McpHealthStatus;
  discovered_tools: McpDiscoveredTool[];
  last_checked_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export type McpModelToolMode = 'direct' | 'on_demand';

export interface McpModelTool {
  name: string;
  label: string;
  kind: 'product' | 'generic';
  mode: McpModelToolMode;
  source_tools: string[];
  description?: string;
}

export interface McpHiddenModelTool {
  name: string;
  label: string;
  description?: string;
  reason: 'quota_exhausted';
  resets_in_seconds: number;
}

export interface McpServerModelView {
  tools: McpModelTool[];
  hidden_tools: McpHiddenModelTool[];
  quota_exhausted?: { group: string; resets_in_seconds: number }[];
}

export interface McpModelView {
  servers: Record<string, McpServerModelView>;
  deferral: {
    on_demand: boolean;
    generic_tool_count: number;
    max_direct_tools: number;
    schema_chars: number;
    max_schema_chars: number;
  };
}
