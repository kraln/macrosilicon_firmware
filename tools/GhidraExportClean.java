// Ghidra headless script: export disassembly and decompilation, skipping empty/data regions
// @category Analysis

import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;
import java.io.*;

public class GhidraExportClean extends GhidraScript {
    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        Listing listing = currentProgram.getListing();
        String name = currentProgram.getName().replace(".bin", "").replace(".BIN", "");

        // Skip empty ROM region. MS2107 default; pass e.g. 0x7C03 for MS2109.
        int skipAbove = 0x7020;
        String[] args = getScriptArgs();
        if (args.length > 0) skipAbove = Integer.decode(args[0]);
        int totalFuncs = 0;
        int skippedFuncs = 0;

        // Export disassembly
        String asmPath = "/tmp/ghidra_" + name + "_disasm.asm";
        PrintWriter asm = new PrintWriter(new FileWriter(asmPath));
        asm.println("; Ghidra disassembly of " + name);

        FunctionIterator funcs = fm.getFunctions(true);
        while (funcs.hasNext()) {
            Function func = funcs.next();
            long addr = func.getEntryPoint().getOffset();
            totalFuncs++;

            if (addr >= skipAbove) {
                skippedFuncs++;
                continue;
            }

            asm.println();
            asm.printf("; === %s @ 0x%04x (size: %d) ===%n",
                func.getName(), addr, func.getBody().getNumAddresses());

            InstructionIterator insts = listing.getInstructions(func.getBody(), true);
            while (insts.hasNext()) {
                Instruction inst = insts.next();
                if (inst.getAddress().getOffset() >= skipAbove) continue;

                String refStr = "";
                for (Reference ref : inst.getReferencesFrom()) {
                    if (ref.getReferenceType().isCall()) {
                        Function target = fm.getFunctionAt(ref.getToAddress());
                        if (target != null) {
                            refStr = "  ; -> " + target.getName();
                        }
                    }
                }
                asm.printf("  0x%04x  %-30s%s%n",
                    inst.getAddress().getOffset(), inst, refStr);
            }
        }

        asm.println();
        asm.printf("; Total functions: %d (skipped %d in empty region 0x%04x+)%n",
            totalFuncs - skippedFuncs, skippedFuncs, skipAbove);
        asm.close();
        println("Wrote disassembly to " + asmPath + " (" + (totalFuncs - skippedFuncs) + " functions)");

        // Export decompilation
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);

        String cPath = "/tmp/ghidra_" + name + "_decompiled.c";
        PrintWriter cFile = new PrintWriter(new FileWriter(cPath));
        cFile.println("/* Ghidra decompilation of " + name + " */");
        cFile.printf("/* Actual code region: 0x0000-0x%04x */%n", skipAbove - 1);
        cFile.println();

        funcs = fm.getFunctions(true);
        while (funcs.hasNext()) {
            Function func = funcs.next();
            long addr = func.getEntryPoint().getOffset();
            if (addr >= skipAbove) continue;

            DecompileResults results = decomp.decompileFunction(func, 30, monitor);
            if (results != null && results.decompileCompleted()) {
                DecompiledFunction df = results.getDecompiledFunction();
                if (df != null) {
                    String c = df.getC();
                    // Skip functions that decompiled to essentially nothing
                    int lineCount = c.split("\n").length;
                    if (lineCount > 500) {
                        cFile.printf("/* === %s @ 0x%04x === */\n", func.getName(), addr);
                        cFile.printf("/* WARNING: %d lines - likely data misidentified as code, truncated */\n\n", lineCount);
                        continue;
                    }
                    cFile.printf("/* === %s @ 0x%04x === */\n", func.getName(), addr);
                    cFile.println(c);
                }
            } else {
                cFile.printf("/* FAILED to decompile %s @ 0x%04x */\n\n", func.getName(), addr);
            }
        }

        decomp.dispose();
        cFile.close();
        println("Wrote decompilation to " + cPath);
    }
}
