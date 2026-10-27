/**
 * Категории и теги эмоциональной окраски для ElevenLabs v4.
 *
 * Аудио-теги позволяют управлять подачей, эмоциями, неречевыми звуками,
 * паузами и акцентами в процессе генерации речи.
 */

export type AudioTagCategory = {
  id: string;
  label: string;
  tags: string[];
};

export const AUDIO_TAG_CATEGORIES: AudioTagCategory[] = [
  {
    id: "mood",
    label: "Настроение и подача",
    tags: [
      "[happy]",
      "[sad]",
      "[excited]",
      "[angry]",
      "[whisper]",
      "[whispers]",
      "[whispering]",
      "[warmly]",
      "[softly]",
      "[gently]",
      "[annoyed]",
      "[appalled]",
      "[thoughtful]",
      "[surprised]",
      "[sarcastic]",
      "[curious]",
      "[curiously]",
      "[crying]",
      "[mischievously]",
      "[professional]",
      "[sympathetic]",
      "[questioning]",
      "[reassuring]",
      "[frustrated]",
      "[impressed]",
      "[delighted]",
      "[amazed]",
      "[nervously]",
      "[alarmed]",
      "[sheepishly]",
      "[desperately]",
      "[deadpan]",
      "[dismissive]",
      "[cute]",
      "[giggles]",
      "[giggling]",
      "[muttering]",
      "[dramatically]",
      "[quietly]",
      "[with controlled fear]",
    ],
  },
  {
    id: "non-speech",
    label: "Неречь и паузы",
    tags: [
      "[short pause]",
      "[long pause]",
      "[pauses]",
      "[Brief pause]",
      "[Gentle pause]",
      "[laughs]",
      "[laughs harder]",
      "[starts laughing]",
      "[laughing]",
      "[laughing hysterically]",
      "[chuckles]",
      "[wheezing]",
      "[sighs]",
      "[frustrated sigh]",
      "[happy gasp]",
      "[exhales]",
      "[exhales sharply]",
      "[inhales deeply]",
      "[clears throat]",
      "[snorts]",
    ],
  },
  {
    id: "sfx",
    label: "SFX (звуковые эффекты)",
    tags: [
      "[applause]",
      "[clapping]",
      "[gunshot]",
      "[explosion]",
      "[swallows]",
      "[gulps]",
      "[fart]",
    ],
  },
  {
    id: "special",
    label: "Особые и акценты",
    tags: [
      "[sings]",
      "[singing]",
      "[singing quickly]",
      "[woo]",
      "[strong Russian accent]",
      "[strong French accent]",
      "[Low, steady voice…]",
      "[robotic voice]",
    ],
  },
];
