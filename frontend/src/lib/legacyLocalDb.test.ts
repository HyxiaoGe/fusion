import { afterEach, describe, expect, it, vi } from 'vitest';

import { dropLegacyLocalDatabase } from './legacyLocalDb';

describe('dropLegacyLocalDatabase', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('deletes the legacy chat mirror database', () => {
    const deleteDatabase = vi.fn();
    vi.stubGlobal('indexedDB', { deleteDatabase });

    dropLegacyLocalDatabase();

    expect(deleteDatabase).toHaveBeenCalledWith('ai-assistant-db');
  });

  it('ignores environments where IndexedDB throws', () => {
    vi.stubGlobal('indexedDB', {
      deleteDatabase: () => {
        throw new Error('blocked');
      },
    });

    expect(() => dropLegacyLocalDatabase()).not.toThrow();
  });
});
