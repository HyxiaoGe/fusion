export interface ModelManagementHealth {
  status: string;
  error?: string | null;
  checked_at?: number | string | null;
}

export interface ModelManagementRegisteredModel {
  model_id: string;
  name: string;
  provider: string;
  provider_display: string;
  health: ModelManagementHealth | string | null;
  selectable: boolean;
  routable: boolean;
  state: string;
  revision: number | null;
  reason?: string | null;
  updated_at?: string | null;
}

export interface ModelManagementSnapshot {
  generated_at: string;
  models: ModelManagementRegisteredModel[];
}

export interface ModelVisibilityUpdateRequest {
  model_id: string;
  selectable: boolean;
  reason: string;
  expected_revision: number | null;
}
