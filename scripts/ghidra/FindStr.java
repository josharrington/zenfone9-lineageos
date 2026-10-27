// Find strings containing a needle, their code references, and decompile the referencing functions.
//@category ZF9
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceManager;
import ghidra.program.util.DefinedDataIterator;
import java.util.*;

public class FindStr extends GhidraScript {
    public void run() throws Exception {
        String[] a = getScriptArgs();
        String needle = a.length > 0 ? a[0] : "";
        boolean decomp = a.length < 2 || !a[1].equals("nodecomp");
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        Set<Address> done = new HashSet<>();
        int nstr = 0;
        for (Data d : DefinedDataIterator.definedStrings(currentProgram)) {
            Object v = d.getValue();
            if (v == null) continue;
            String s = v.toString();
            if (!s.contains(needle)) continue;
            nstr++;
            println("STRING " + d.getAddress() + "  " + s.replace("\n", "\\n"));
            for (Reference r : rm.getReferencesTo(d.getAddress())) {
                Function f = fm.getFunctionContaining(r.getFromAddress());
                println("   REF " + r.getFromAddress() + " in " +
                        (f != null ? f.getName() + "@" + f.getEntryPoint() : "<no function>"));
                if (decomp && f != null && !done.contains(f.getEntryPoint())) {
                    done.add(f.getEntryPoint());
                    DecompileResults res = di.decompileFunction(f, 90, monitor);
                    if (res != null && res.decompileCompleted()) {
                        println("===== DECOMP " + f.getName() + " @ " + f.getEntryPoint() + " =====");
                        println(res.getDecompiledFunction().getC());
                    } else {
                        println("   (decompile failed)");
                    }
                }
            }
        }
        println("TOTAL strings matched: " + nstr);
    }
}
