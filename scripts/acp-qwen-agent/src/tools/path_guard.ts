import fs from "node:fs";
import path from "node:path";

export class PathGuardError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PathGuardError";
  }
}

function isInsideWorkspace(workspaceRoot: string, candidate: string): boolean {
  const root = workspaceRoot.endsWith(path.sep)
    ? workspaceRoot
    : workspaceRoot + path.sep;
  return candidate === workspaceRoot || candidate.startsWith(root);
}

/**
 * Resolve `userPath` against the workspace and require the real path to stay inside.
 * Rejects `..` escapes, absolute paths outside workspace, and symlink escapes.
 */
export function resolveInsideWorkspace(
  workspaceRoot: string,
  userPath: string,
): string {
  if (!workspaceRoot || !path.isAbsolute(workspaceRoot)) {
    throw new PathGuardError("workspace root must be an absolute path");
  }
  if (typeof userPath !== "string" || userPath.trim().length === 0) {
    throw new PathGuardError("path is required");
  }

  const rootReal = fs.realpathSync.native(workspaceRoot);
  const joined = path.isAbsolute(userPath)
    ? path.normalize(userPath)
    : path.normalize(path.join(rootReal, userPath));

  // If the path exists, resolve symlinks; otherwise resolve the nearest existing parent.
  let realCandidate: string;
  try {
    realCandidate = fs.realpathSync.native(joined);
  } catch {
    let parent = path.dirname(joined);
    let leaf = path.basename(joined);
    const parts: string[] = [leaf];
    while (true) {
      try {
        const realParent = fs.realpathSync.native(parent);
        realCandidate = path.normalize(path.join(realParent, ...parts.reverse()));
        break;
      } catch {
        const nextParent = path.dirname(parent);
        if (nextParent === parent) {
          throw new PathGuardError(`path does not resolve inside workspace: ${userPath}`);
        }
        parts.push(path.basename(parent));
        parent = nextParent;
      }
    }
  }

  if (!isInsideWorkspace(rootReal, realCandidate)) {
    throw new PathGuardError(
      `path escapes workspace: ${userPath} -> ${realCandidate}`,
    );
  }
  return realCandidate;
}

export function toWorkspaceRelative(
  workspaceRoot: string,
  absolutePath: string,
): string {
  const rootReal = fs.realpathSync.native(workspaceRoot);
  const rel = path.relative(rootReal, absolutePath);
  if (rel.startsWith("..") || path.isAbsolute(rel)) {
    throw new PathGuardError("path is outside workspace");
  }
  return rel.length === 0 ? "." : rel;
}
