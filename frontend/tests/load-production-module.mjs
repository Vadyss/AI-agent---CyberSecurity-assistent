// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const require = createRequire(import.meta.url);
const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url));

// Exercise the actual TypeScript/React modules using the existing compiler,
// without a second runner or copied implementations.
export function productionModule(relativePath, overrides = {}) {
  const cache = new Map();
  function load(path) {
    if (cache.has(path)) return cache.get(path).exports;
    const source = readFileSync(path, "utf8");
    const compiled = ts.transpileModule(source, {
      fileName: path,
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
    }).outputText;
    const loadedModule = { exports: {} };
    cache.set(path, loadedModule);
    const localRequire = (specifier) => {
      if (Object.hasOwn(overrides, specifier)) return overrides[specifier];
      if (specifier.startsWith(".") || specifier.startsWith("@/")) {
        let target = specifier.startsWith("@/")
          ? resolve(sourceRoot, specifier.slice(2))
          : resolve(dirname(path), specifier);
        if (!extname(target)) {
          try {
            readFileSync(`${target}.ts`);
            target += ".ts";
          } catch {
            target += ".tsx";
          }
        }
        return load(target);
      }
      return require(specifier);
    };
    new Function("require", "module", "exports", compiled)(localRequire, loadedModule, loadedModule.exports);
    return loadedModule.exports;
  }
  return load(resolve(sourceRoot, relativePath));
}
