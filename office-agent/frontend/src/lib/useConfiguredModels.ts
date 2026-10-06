import { useEffect, useState } from "react";
import { getProviders } from "./rest";

/** "provider:model" for every provider set up with a default model. */
export function useConfiguredModels(): string[] {
  const [models, setModels] = useState<string[]>([]);
  useEffect(() => {
    getProviders()
      .then((providers) =>
        setModels(
          Object.entries(providers)
            .filter(([, info]) => info.default_model)
            .map(([key, info]) => `${key}:${info.default_model}`),
        ),
      )
      .catch(() => {});
  }, []);
  return models;
}
