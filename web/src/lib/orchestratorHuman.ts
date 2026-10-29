export interface HumanError {
  what: string;
  why: string;
  todo: string;
}

function sentence(text: string): string {
  const t = text.trim();
  if (!t) return "";
  return /[.!?…]$/.test(t) ? t : `${t}.`;
}

export function humanErrorText(error: HumanError): string {
  return [error.what, error.why, error.todo].map(sentence).filter(Boolean).join(" ");
}

const CYRILLIC = /[а-яё]/i;
const TECHNICAL = /[{}[\]<>]|Error\b|Traceback|\b[a-z]+_[a-z_]+\b|\b[0-9a-f]{12,}\b|\bHTTP\b|\bnull\b|\bundefined\b/;

export function isHumanText(text: string): boolean {
  const t = text.trim();
  return Boolean(t) && t.length <= 400 && CYRILLIC.test(t) && !TECHNICAL.test(t);
}

function serverWhy(detail: string, fallback: string): string {
  return isHumanText(detail) ? detail.trim() : fallback;
}

export function humanOrchError(status: number, detail = ""): HumanError {
  if (status === 0) {
    return {
      what: "Нет связи с сервером",
      why: serverWhy(detail, "Студия не ответила или соединение оборвалось"),
      todo: "Проверьте, что студия запущена, и повторите",
    };
  }
  if (status === 401 || status === 403) {
    return {
      what: "Нет доступа к оркестратору",
      why: serverWhy(detail, "Сервер не принял вход студии"),
      todo: "Обновите страницу и войдите заново. Если не помогло, попросите администратора открыть чат для входа студии",
    };
  }
  if (status === 404) {
    return {
      what: "Сервер не нашёл то, к чему относится запрос",
      why: serverWhy(detail, "Подтверждение уже обработано, задача завершилась или сервер ещё не обновлён"),
      todo: "Обновите чат и проверьте статус задачи",
    };
  }
  if (status === 409) {
    return {
      what: "Изменение не записано",
      why: serverWhy(detail, "Пока вы смотрели, состояние на сервере изменилось"),
      todo: "Обновите данные и решите заново",
    };
  }
  if (status === 423) {
    return {
      what: "Действие сейчас заблокировано",
      why: serverWhy(detail, "Включён стоп или идёт платный шаг"),
      todo: "Дождитесь конца шага или снимите стоп во вкладке «Регистратор»",
    };
  }
  if (status === 429) {
    return {
      what: "Слишком много запросов подряд",
      why: serverWhy(detail, "Сервер временно ограничил частоту"),
      todo: "Подождите минуту и повторите",
    };
  }
  if (status === 400 || status === 422) {
    return {
      what: "Сервер не принял запрос",
      why: serverWhy(detail, "Данные запроса не прошли проверку"),
      todo: "Переформулируйте сообщение или повторите действие",
    };
  }
  if (status >= 500) {
    return {
      what: "Сервер не смог выполнить запрос",
      why: serverWhy(detail, "Внутренняя ошибка на сервере"),
      todo: "Повторите через минуту. Если не проходит, откройте «Логи»",
    };
  }
  return {
    what: "Запрос не выполнен",
    why: serverWhy(detail, "Сервер ответил отказом"),
    todo: "Повторите действие",
  };
}

export const RISK_RU: Record<string, string> = {
  none: "без риска",
  low: "низкий",
  medium: "средний",
  high: "высокий",
  read: "только чтение",
  local_safe: "локально",
  paid: "платное",
  destructive: "стирает данные",
};

export function riskRu(risk: string): string {
  if (!risk) return "";
  return RISK_RU[risk] ?? "не указан";
}

export const RISK_TONE: Record<string, "muted" | "info" | "warning" | "destructive"> = {
  none: "muted",
  read: "muted",
  low: "info",
  local_safe: "info",
  medium: "warning",
  paid: "warning",
  high: "destructive",
  destructive: "destructive",
};

export const AGENT_RU: Record<string, string> = {
  supervisor: "Ведущий",
  planner: "Планировщик",
  critic: "Критик",
  executor: "Исполнитель",
  classifier: "Разбор реплики",
  summarizer: "Итоги",
};

const AGENT_STATUS_RU: Record<string, string> = {
  idle: "ждёт",
  running: "работает",
  done: "готово",
  waiting: "ждёт решения",
  waiting_approval: "ждёт подтверждения",
  paused: "пауза",
  queued: "в очереди",
  cancelled: "отменён",
  killed: "остановлен",
  failed: "ошибка",
  planning: "составляет план",
  critic_review: "проверяет план",
  executing: "выполняет",
};

export function agentStatusRu(status: string): string {
  if (!status) return "";
  if (AGENT_STATUS_RU[status]) return AGENT_STATUS_RU[status];
  return CYRILLIC.test(status) ? status : "занят";
}

export const MODE_RU: Record<string, string> = {
  interrupt: "прерывание",
  steer: "уточнение к задаче",
  append: "новый запрос",
  task: "новая задача в очередь",
};

const VALIDATOR_RU: Record<string, string> = {
  plan_sanity: "проверка плана",
  plan_missing_verify: "проверка перед запуском",
  no_deletions: "запрет удаления",
  spend_guard: "контроль трат",
  shot_rules: "правила кадров",
  vo_length: "длина закадра",
  snapshot_kadry: "снимок кадров",
};

export function validatorRu(name: string): string {
  return VALIDATOR_RU[name] ?? (CYRILLIC.test(name) ? name : "проверка");
}

const JOURNAL_HINT = "Подробности — во вкладке «Регистратор» → «Журнал»";

const PAID_UNKNOWN: HumanError = {
  what: "Неизвестно, прошла ли оплата платного шага",
  why: "Повтор сам не запускается, чтобы не заплатить дважды",
  todo: "Проверьте результат в кабинете провайдера и решите вручную",
};

const TASK_FAILURE_RU: Record<string, HumanError> = {
  validator_blocked: {
    what: "Проверка плана не пропустила задачу",
    why: "Автоматическая проверка нашла в плане шаг, который нельзя выполнить: такого действия нет среди разрешённых или перед запуском не хватает проверки",
    todo: "Переформулируйте просьбу проще или разбейте её на отдельные шаги. Какие проверки не прошли — в списке «Задачи»",
  },
  step_failed: {
    what: "Шаг задачи не выполнился",
    why: "Действие вернуло ошибку",
    todo: `Посмотрите шаг в списке «Задачи» и попросите повторить. ${JOURNAL_HINT}`,
  },
  action_rejected: {
    what: "Сервер отказался выполнять шаг",
    why: "Действие не разрешено или его параметры не прошли проверку",
    todo: "Уточните, что именно сделать, и отправьте ещё раз",
  },
  partial_write: {
    what: "Шаг записал изменения не полностью",
    why: "Часть изменений сохранилась, часть нет",
    todo: "Проверьте проект и решите, повторять ли шаг",
  },
  paid_outcome_unknown: PAID_UNKNOWN,
  paid_unknown_outcome: PAID_UNKNOWN,
  paid_step_failed: {
    what: "Платный шаг не удался",
    why: "Провайдер вернул ошибку",
    todo: "Прежде чем повторять, проверьте, списались ли деньги",
  },
  drive_failed: {
    what: "Задача остановилась из-за внутренней ошибки",
    why: "Сбой в работе оркестратора",
    todo: `Повторите просьбу. ${JOURNAL_HINT}`,
  },
  plan_stopped: {
    what: "Задача остановлена до конца плана",
    why: "Выполнение прервали: стоп или пауза",
    todo: "Если нужно, попросите продолжить",
  },
  parallel_risk: {
    what: "План не запущен",
    why: "Шаги с риском нельзя выполнять одновременно",
    todo: "Попросите выполнить их по очереди",
  },
  rejected: {
    what: "Вы отклонили шаг",
    why: "Это ваше решение, а не сбой: ничего не изменено",
    todo: "Если передумали, повторите просьбу",
  },
  unapproved_action: {
    what: "Изменение не записано",
    why: "Шаг меняет проект, а подтверждения человеком не было",
    todo: "Попросите повторить и подтвердите карточку",
  },
  critic_reject: {
    what: "Критик не принял план",
    why: "План показался ему рискованным или неполным",
    todo: "Опишите задачу подробнее и отправьте ещё раз",
  },
  not_in_whitelist: {
    what: "Такого действия нет в списке разрешённых",
    why: "Оркестратор предложил шаг, который ему запрещён",
    todo: "Переформулируйте просьбу",
  },
  deletion_forbidden: {
    what: "Удаление запрещено",
    why: "Кадры и сцены нельзя удалять, только мягко скрывать",
    todo: "Попросите скрыть, а не удалить",
  },
  interrupted_by_restart: {
    what: "Задача остановилась: сервер перезапускался",
    why: "Сервер перезапустился, пока задача шла, и она не дошла до конца",
    todo: "Попросите повторить — задача начнётся заново",
  },
};

export function taskFailureRu(error: string, reason = ""): HumanError | null {
  return TASK_FAILURE_RU[reason] ?? TASK_FAILURE_RU[error] ?? null;
}

export function humanTaskFailure(error: string, reason = ""): HumanError {
  const known = taskFailureRu(error, reason);
  if (known) return known;
  return {
    what: "Задача не выполнена",
    why: isHumanText(error) ? error.trim() : "Задача остановлена из-за внутренней ошибки",
    todo: `Попросите повторить другими словами. ${JOURNAL_HINT}`,
  };
}

export function humanDetail(text: string, fallback: string): string {
  return isHumanText(text) ? text.trim() : fallback;
}

export function actionLabel(action: string, titles: Record<string, string>): string {
  if (!action) return "";
  const known = titles[action];
  if (known) return known;
  return CYRILLIC.test(action) ? action : "";
}

export function formatUsd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "";
  const rounded = Math.round(value * 100) / 100;
  return `около $${rounded}`;
}
