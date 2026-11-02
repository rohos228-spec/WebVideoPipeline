/**
 * Чистые патчи данных нод канваса.
 *
 * Выбор модели — строго per-step: модель, выбранная в ноде, меняет только эту
 * ноду и помечает её modelPinned. Глобальный пикер (шапка) обновляет только
 * текстовые ноды без явного выбора.
 */

type NodeLike = { id: string; data: Record<string, unknown> };

export function applyNodeDataPatch<T extends NodeLike>(
  nodes: T[],
  nodeKey: string,
  patch: Record<string, unknown>,
): T[] {
  const pin = patch.modelId != null;
  return nodes.map((n) => {
    if (n.id !== nodeKey) return n;
    return {
      ...n,
      data: {
        ...n.data,
        ...patch,
        ...(pin ? { modelPinned: true } : {}),
      },
    } as T;
  });
}

export function applyGlobalTextModel<T extends NodeLike>(
  nodes: T[],
  modelId: string,
  isTextNode: (n: T) => boolean,
): T[] {
  let changed = false;
  const next = nodes.map((n) => {
    if (!isTextNode(n) || n.data.modelPinned === true) return n;
    if (n.data.modelId === modelId) return n;
    changed = true;
    return { ...n, data: { ...n.data, modelId } } as T;
  });
  return changed ? next : nodes;
}
