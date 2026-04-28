const fs = require("fs");
const path = require("path");

const SUPPORTED_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"]);
const EXCLUDED_DIRS = new Set(["node_modules", ".git", "dist", "build", ".next", "coverage", "out"]);

function exists(target) {
  try {
    return fs.existsSync(target);
  } catch {
    return false;
  }
}

function loadRequest() {
  const raw = fs.readFileSync(0, "utf8").trim();
  return raw ? JSON.parse(raw) : {};
}

function respond(payload) {
  process.stdout.write(JSON.stringify(payload));
}

function fail(message) {
  respond({ ok: false, error: String(message || "Unknown error") });
  process.exit(0);
}

function findTypescriptLib(filePath) {
  if (process.env.TYPESCRIPT_LIB_PATH && exists(process.env.TYPESCRIPT_LIB_PATH)) {
    return process.env.TYPESCRIPT_LIB_PATH;
  }
  const start = path.dirname(path.resolve(filePath));
  const candidates = [];
  let current = start;
  while (true) {
    candidates.push(path.join(current, "node_modules", "typescript", "lib", "typescript.js"));
    candidates.push(path.join(current, "frontend", "node_modules", "typescript", "lib", "typescript.js"));
    const parent = path.dirname(current);
    if (parent === current) {
      break;
    }
    current = parent;
  }
  for (const candidate of candidates) {
    if (exists(candidate)) {
      return candidate;
    }
  }
  return "";
}

function findProjectRoot(filePath) {
  let current = path.dirname(path.resolve(filePath));
  while (true) {
    if (
      exists(path.join(current, "tsconfig.json")) ||
      exists(path.join(current, "jsconfig.json")) ||
      exists(path.join(current, "package.json"))
    ) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      break;
    }
    current = parent;
  }
  return path.dirname(path.resolve(filePath));
}

function collectFallbackFiles(rootDir, targetFile) {
  const resolvedTarget = path.resolve(targetFile);
  const results = [];
  const queue = [rootDir];
  const seen = new Set();
  while (queue.length > 0 && results.length < 1000) {
    const current = queue.pop();
    if (seen.has(current)) {
      continue;
    }
    seen.add(current);
    let entries = [];
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (!EXCLUDED_DIRS.has(entry.name)) {
          queue.push(fullPath);
        }
        continue;
      }
      if (SUPPORTED_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) {
        results.push(path.resolve(fullPath));
      }
    }
  }
  if (!results.includes(resolvedTarget)) {
    results.push(resolvedTarget);
  }
  return Array.from(new Set(results));
}

function getProjectInfo(ts, rootDir, targetFile) {
  const configPath = ts.findConfigFile(rootDir, ts.sys.fileExists, "tsconfig.json") || ts.findConfigFile(rootDir, ts.sys.fileExists, "jsconfig.json");
  if (configPath) {
    const configFile = ts.readConfigFile(configPath, ts.sys.readFile);
    if (!configFile.error) {
      const parsed = ts.parseJsonConfigFileContent(configFile.config, ts.sys, path.dirname(configPath), undefined, configPath);
      const files = Array.from(new Set((parsed.fileNames || []).map((name) => path.resolve(name))));
      const resolvedTarget = path.resolve(targetFile);
      if (!files.includes(resolvedTarget)) {
        files.push(resolvedTarget);
      }
      return {
        rootDir: path.dirname(configPath),
        files,
        options: parsed.options || {},
      };
    }
  }
  return {
    rootDir,
    files: collectFallbackFiles(rootDir, targetFile),
    options: {
      allowJs: true,
      checkJs: false,
      jsx: ts.JsxEmit.ReactJSX,
      target: ts.ScriptTarget.ES2020,
      module: ts.ModuleKind.ESNext,
      moduleResolution: ts.ModuleResolutionKind.NodeJs,
      esModuleInterop: true,
      allowSyntheticDefaultImports: true,
      resolveJsonModule: true,
      skipLibCheck: true,
    },
  };
}

function createService(ts, rootDir, targetFile) {
  const info = getProjectInfo(ts, rootDir, targetFile);
  const files = Array.from(new Set(info.files.map((name) => path.resolve(name))));
  const host = {
    getScriptFileNames: () => files,
    getScriptVersion: () => "0",
    getScriptSnapshot: (fileName) => {
      if (!exists(fileName)) {
        return undefined;
      }
      return ts.ScriptSnapshot.fromString(fs.readFileSync(fileName, "utf8"));
    },
    getCurrentDirectory: () => info.rootDir,
    getCompilationSettings: () => info.options,
    getDefaultLibFileName: (options) => ts.getDefaultLibFilePath(options),
    fileExists: ts.sys.fileExists,
    readFile: ts.sys.readFile,
    readDirectory: ts.sys.readDirectory,
    directoryExists: ts.sys.directoryExists,
    getDirectories: ts.sys.getDirectories,
  };
  return ts.createLanguageService(host, ts.createDocumentRegistry());
}

function getSourceFile(ts, service, filePath) {
  const program = typeof service.getProgram === "function" ? service.getProgram() : undefined;
  const fromProgram = program && typeof program.getSourceFile === "function" ? program.getSourceFile(filePath) : undefined;
  if (fromProgram) {
    return fromProgram;
  }
  return ts.createSourceFile(filePath, fs.readFileSync(filePath, "utf8"), ts.ScriptTarget.Latest, true);
}

function getPosition(ts, service, filePath, line, column) {
  const sourceFile = getSourceFile(ts, service, filePath);
  const safeLine = Math.max(0, Number(line || 1) - 1);
  const safeColumn = Math.max(0, Number(column || 0));
  return ts.getPositionOfLineAndCharacter(sourceFile, safeLine, safeColumn);
}

function getLocation(ts, service, filePath, position) {
  const sourceFile = getSourceFile(ts, service, filePath);
  const data = ts.getLineAndCharacterOfPosition(sourceFile, position);
  return { line: data.line + 1, column: data.character };
}

function getSpanLocation(ts, service, filePath, span) {
  return getLocation(ts, service, filePath, span.start);
}

function flattenNavTree(ts, service, filePath, node, output) {
  if (!node) {
    return;
  }
  const span = Array.isArray(node.spans) && node.spans.length > 0 ? node.spans[0] : null;
  if (span && node.text && node.text !== "<global>") {
    const location = getSpanLocation(ts, service, filePath, span);
    output.push({
      name: node.text,
      kind: node.kind || "symbol",
      line: location.line,
      column: location.column,
    });
  }
  for (const child of node.childItems || []) {
    flattenNavTree(ts, service, filePath, child, output);
  }
}

function findNamedPosition(ts, service, filePath, symbolName) {
  const sourceFile = getSourceFile(ts, service, filePath);
  let found = -1;
  function visit(node) {
    if (found !== -1) {
      return;
    }
    const name = node && node.name && ts.isIdentifier(node.name) ? node.name.text : "";
    if (
      name === symbolName && (
        ts.isFunctionDeclaration(node) ||
        ts.isMethodDeclaration(node) ||
        ts.isFunctionExpression(node) ||
        ts.isArrowFunction(node) ||
        ts.isClassDeclaration(node) ||
        ts.isVariableDeclaration(node)
      )
    ) {
      found = node.name.getStart(sourceFile);
      return;
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return found;
}

function normalizeDefinition(ts, service, item) {
  const filePath = path.resolve(item.fileName || item.file || "");
  const location = getSpanLocation(ts, service, filePath, item.textSpan || item.span);
  return {
    name: item.name || item.containerName || path.basename(filePath),
    kind: item.kind || "symbol",
    file: filePath,
    line: location.line,
    column: location.column,
  };
}

function normalizeReference(ts, service, item) {
  const filePath = path.resolve(item.fileName || item.file || "");
  const location = getSpanLocation(ts, service, filePath, item.textSpan || item.span);
  return {
    file: filePath,
    line: location.line,
    column: location.column,
    isDefinition: Boolean(item.isDefinition),
    isWriteAccess: Boolean(item.isWriteAccess),
  };
}

function normalizeCallItem(ts, service, item, fallbackSpans, preferFallbackSpans) {
  const filePath = path.resolve(item.file || item.fileName || "");
  const fallbackSpan = Array.isArray(fallbackSpans) && fallbackSpans.length > 0 ? fallbackSpans[0] : null;
  const span = preferFallbackSpans
    ? (fallbackSpan || item.selectionSpan || item.span || null)
    : (item.selectionSpan || item.span || fallbackSpan || null);
  const location = span ? getSpanLocation(ts, service, filePath, span) : { line: 1, column: 0 };
  return {
    name: item.name || path.basename(filePath),
    kind: item.kind || "symbol",
    file: filePath,
    line: location.line,
    column: location.column,
  };
}

function getCallHierarchyCalls(service, direction, filePath, position, preparedItem) {
  const methodName = direction === "incoming" ? "provideCallHierarchyIncomingCalls" : "provideCallHierarchyOutgoingCalls";
  const method = service[methodName];
  if (typeof method !== "function") {
    return [];
  }
  try {
    return method.call(service, filePath, position) || [];
  } catch {
    try {
      return method.call(service, preparedItem) || [];
    } catch {
      return [];
    }
  }
}

function main() {
  const request = loadRequest();
  const filePath = path.resolve(String(request.file_path || ""));
  const operation = String(request.operation || "");
  if (!filePath || !SUPPORTED_EXTENSIONS.has(path.extname(filePath).toLowerCase())) {
    fail("Unsupported TypeScript/JavaScript file");
  }
  if (!exists(filePath)) {
    fail(`File not found: ${filePath}`);
  }
  const tsLibPath = findTypescriptLib(filePath);
  if (!tsLibPath) {
    fail("TypeScript language service is not available");
  }
  const ts = require(tsLibPath);
  const rootDir = findProjectRoot(filePath);
  const service = createService(ts, rootDir, filePath);
  if (operation === "goto_definition") {
    const position = getPosition(ts, service, filePath, request.line, request.column);
    const result = typeof service.getDefinitionAndBoundSpan === "function"
      ? service.getDefinitionAndBoundSpan(filePath, position)
      : { definitions: service.getDefinitionAtPosition(filePath, position) || [] };
    const definitions = (result && result.definitions ? result.definitions : []).map((item) => normalizeDefinition(ts, service, item));
    respond({ ok: true, definitions });
    return;
  }
  if (operation === "find_references") {
    const position = getPosition(ts, service, filePath, request.line, request.column);
    const groups = typeof service.findReferences === "function" ? service.findReferences(filePath, position) || [] : [];
    const references = [];
    const seen = new Set();
    for (const group of groups) {
      const symbolName = group && group.definition ? (group.definition.name || "") : "";
      const symbolKind = group && group.definition ? (group.definition.kind || "") : "";
      for (const item of group.references || []) {
        const normalized = normalizeReference(ts, service, item);
        if (symbolName) {
          normalized.name = symbolName;
        }
        if (symbolKind) {
          normalized.kind = symbolKind;
        }
        const key = `${normalized.file}:${normalized.line}:${normalized.column}`;
        if (!seen.has(key)) {
          seen.add(key);
          references.push(normalized);
        }
      }
    }
    respond({ ok: true, references });
    return;
  }
  if (operation === "document_symbols") {
    const tree = typeof service.getNavigationTree === "function" ? service.getNavigationTree(filePath) : null;
    const symbols = [];
    flattenNavTree(ts, service, filePath, tree, symbols);
    respond({ ok: true, symbols });
    return;
  }
  if (operation === "call_hierarchy") {
    const symbolName = String(request.symbol_name || "");
    const symbolPosition = findNamedPosition(ts, service, filePath, symbolName);
    if (symbolPosition < 0) {
      fail(`Symbol not found: ${symbolName}`);
    }
    if (typeof service.prepareCallHierarchy !== "function") {
      fail("Call hierarchy is not available in this TypeScript runtime");
    }
    const prepared = service.prepareCallHierarchy(filePath, symbolPosition);
    const items = Array.isArray(prepared) ? prepared : prepared ? [prepared] : [];
    const targetItem = items[0];
    if (!targetItem) {
      respond({ ok: true, incoming: [], outgoing: [] });
      return;
    }
    const incomingRaw = getCallHierarchyCalls(service, "incoming", filePath, symbolPosition, targetItem);
    const outgoingRaw = getCallHierarchyCalls(service, "outgoing", filePath, symbolPosition, targetItem);
    const incoming = incomingRaw.map((entry) => normalizeCallItem(ts, service, entry.from || entry, entry.fromSpans || entry.spans || [], true)).filter((item) => item.file);
    const outgoing = outgoingRaw.map((entry) => normalizeCallItem(ts, service, entry.to || entry, entry.fromSpans || entry.spans || [], false)).filter((item) => item.file);
    respond({ ok: true, incoming, outgoing });
    return;
  }
  fail(`Unsupported operation: ${operation}`);
}

try {
  main();
} catch (error) {
  fail(error && error.stack ? error.stack : String(error));
}
