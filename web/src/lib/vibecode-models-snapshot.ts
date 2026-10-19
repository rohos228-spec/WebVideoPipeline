/** Снимок каталога vibecode.moe */

export type VibecodeSnapshotModel = {
  id: string;
  display_name: string;
  object?: string;
  owned_by?: string;
  is_image: boolean;
  is_video?: boolean;
  pricing: {
    currency?: string;
    input_usd_per_m?: number | null;
    output_usd_per_m?: number | null;
    cache_read_usd_per_m?: number | null;
    cache_create_usd_per_m?: number | null;
    usd_per_image?: number | null;
    [key: string]: any;
  };
};

export const VIBECODE_MODELS_SNAPSHOT: VibecodeSnapshotModel[] = [
  {
    "display_name": "Claude Sonnet 5.5",
    "id": "claude-sonnet-5-5",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.02,
      "currency": "usd",
      "input_usd_per_m": 0.2,
      "output_usd_per_m": 0.98
    }
  },
  {
    "display_name": "Claude Opus 5.5",
    "id": "claude-opus-5-5",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.039,
      "currency": "usd",
      "input_usd_per_m": 0.39,
      "output_usd_per_m": 1.97
    }
  },
  {
    "display_name": "Claude Haiku 4.5",
    "id": "claude-haiku-4-5",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.02,
      "currency": "usd",
      "input_usd_per_m": 0.2,
      "output_usd_per_m": 0.98
    }
  },
  {
    "display_name": "Claude Fable 5.1",
    "id": "claude-fable-5-1",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.47,
      "currency": "usd",
      "input_usd_per_m": 4.71,
      "output_usd_per_m": 23.53
    }
  },
  {
    "display_name": "Claude Fable 5",
    "id": "claude-fable-5",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.47,
      "currency": "usd",
      "input_usd_per_m": 4.71,
      "output_usd_per_m": 23.53
    }
  },
  {
    "display_name": "GPT 6 Sol",
    "id": "gpt-6-sol",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.017,
      "currency": "usd",
      "input_usd_per_m": 0.17,
      "output_usd_per_m": 0.85
    }
  },
  {
    "display_name": "GPT 6.1 Sol",
    "id": "gpt-6.1-sol",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.017,
      "currency": "usd",
      "input_usd_per_m": 0.17,
      "output_usd_per_m": 0.85
    }
  },
  {
    "display_name": "GPT 6 Astra",
    "id": "gpt-6-astra",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.085,
      "currency": "usd",
      "input_usd_per_m": 0.85,
      "output_usd_per_m": 4.26
    }
  },
  {
    "display_name": "GPT 5.6 Sol",
    "id": "gpt-5.6-sol",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.043,
      "currency": "usd",
      "input_usd_per_m": 0.43,
      "output_usd_per_m": 2.56
    }
  },
  {
    "display_name": "GPT 6 Luna",
    "id": "gpt-6-luna",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.0013,
      "currency": "usd",
      "input_usd_per_m": 0.013,
      "output_usd_per_m": 0.066
    }
  },
  {
    "display_name": "Gemini 3.8 Flash",
    "id": "gemini-3.8-flash",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.015,
      "currency": "usd",
      "input_usd_per_m": 0.15,
      "output_usd_per_m": 0.74
    }
  },
  {
    "display_name": "Gemini 3 Flash",
    "id": "gemini-3-flash-preview",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.0098,
      "currency": "usd",
      "input_usd_per_m": 0.098,
      "output_usd_per_m": 0.59
    }
  },
  {
    "display_name": "Grok 4.7",
    "id": "grok-4-7",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.0098,
      "currency": "usd",
      "input_usd_per_m": 0.098,
      "output_usd_per_m": 0.3
    }
  },
  {
    "display_name": "GLM 5.3 Flash",
    "id": "glm-5.3-flash",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.0039,
      "currency": "usd",
      "input_usd_per_m": 0.039,
      "output_usd_per_m": 0.14
    }
  },
  {
    "display_name": "GLM 5.3 FlashX",
    "id": "glm-5.3-flashx",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.0098,
      "currency": "usd",
      "input_usd_per_m": 0.098,
      "output_usd_per_m": 0.34
    }
  },
  {
    "display_name": "GPT Image 2",
    "id": "gpt-image-2",
    "is_image": true,
    "object": "model",
    "owned_by": "outsee",
    "pricing": {
      "currency": "usd",
      "usd_per_image": 0.042646
    }
  },
  {
    "display_name": "Nano Banana",
    "id": "nano-banana",
    "is_image": true,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "currency": "usd",
      "usd_per_image": 0.045927
    }
  },
  {
    "display_name": "Nano Banana 2 (1K/2K/4K)",
    "id": "nano-banana-2",
    "is_image": true,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "currency": "usd",
      "usd_per_image": 0.039366
    }
  },
  {
    "display_name": "Nano Banana Pro (1K/2K/4K)",
    "id": "nano-banana-pro",
    "is_image": true,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "currency": "usd",
      "usd_per_image": 0.059049
    }
  },
  {
    "display_name": "Nano Banana 2 Lite",
    "id": "nano-banana-2-lite",
    "is_image": true,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "currency": "usd",
      "usd_per_image": 0.016402
    }
  }
];
