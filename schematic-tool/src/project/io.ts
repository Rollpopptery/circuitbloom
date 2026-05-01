import type { Schematic } from "../types";
import type { ComponentParams } from "../state/store";
import type { AnalysisConfig } from "../spice/formatter";

export interface ProjectFile {
  version: "1";
  schematic: Schematic;
  componentParams: Record<string, ComponentParams>;
  analysisConfig?: AnalysisConfig;
}

function projectBlob(
  schematic: Schematic,
  componentParams: Record<string, ComponentParams>,
  analysisConfig: AnalysisConfig,
): Blob {
  const project: ProjectFile = { version: "1", schematic, componentParams, analysisConfig };
  return new Blob([JSON.stringify(project, null, 2)], { type: "application/json" });
}

function suggestedName(kicadFilename: string): string {
  return kicadFilename.replace(/\.kicad_sch$/i, "") + ".spice-project.json";
}

// Saved handle so subsequent saves overwrite the same file
let _fileHandle: FileSystemFileHandle | null = null;

export async function saveProject(
  schematic: Schematic,
  componentParams: Record<string, ComponentParams>,
  analysisConfig: AnalysisConfig,
  kicadFilename: string,
  forcePickNew = false,
): Promise<void> {
  const blob = projectBlob(schematic, componentParams, analysisConfig);

  // Use File System Access API if available
  if ("showSaveFilePicker" in window) {
    try {
      if (!_fileHandle || forcePickNew) {
        _fileHandle = await (window as any).showSaveFilePicker({
          suggestedName: suggestedName(kicadFilename),
          types: [{ description: "SPICE Project", accept: { "application/json": [".json"] } }],
        });
      }
      const writable = await _fileHandle!.createWritable();
      await writable.write(blob);
      await writable.close();
      return;
    } catch (e: any) {
      if (e?.name === "AbortError") return; // user cancelled picker
      _fileHandle = null; // handle went stale, fall through to download
    }
  }

  // Fallback: classic anchor download
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = suggestedName(kicadFilename);
  a.click();
  URL.revokeObjectURL(url);
}

export function clearSaveHandle(): void {
  _fileHandle = null;
}

export function parseProjectFile(text: string): ProjectFile {
  const data = JSON.parse(text) as Record<string, unknown>;
  if (data.version !== "1" || !data.schematic) {
    throw new Error("Not a valid .spice-project.json file");
  }
  return {
    version: "1",
    schematic: data.schematic as Schematic,
    componentParams: (data.componentParams ?? {}) as Record<string, ComponentParams>,
    analysisConfig: data.analysisConfig as AnalysisConfig | undefined,
  };
}
