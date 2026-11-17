# Read-only: list handlers Windows offers for .pdf in its "choose an app" UI (SHAssocEnumHandlers).
$code = @"
using System; using System.Runtime.InteropServices; using System.Collections.Generic;
[ComImport, Guid("F04061AC-1659-4a3f-A954-775AA57FC083"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IAssocHandler {
  [PreserveSig] int GetName([MarshalAs(UnmanagedType.LPWStr)] out string n);
  [PreserveSig] int GetUIName([MarshalAs(UnmanagedType.LPWStr)] out string n);
  [PreserveSig] int GetIconLocation([MarshalAs(UnmanagedType.LPWStr)] out string p, out int i);
  [PreserveSig] int IsRecommended(); }
[ComImport, Guid("973810ae-9599-4b88-9e4d-6ee98c9552da"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IEnumAssocHandlers { [PreserveSig] int Next(uint c, [MarshalAs(UnmanagedType.Interface)] out IAssocHandler h, out uint f); }
public static class AH {
  [DllImport("shell32.dll", CharSet=CharSet.Unicode)] static extern int SHAssocEnumHandlers(string ext, int filter, out IEnumAssocHandlers e);
  public static List<string> List(string ext, int filter) { var r = new List<string>(); IEnumAssocHandlers e; if (SHAssocEnumHandlers(ext, filter, out e) != 0) return r;
    IAssocHandler h; uint f; while (e.Next(1, out h, out f) == 0 && f == 1) { string n, u; h.GetName(out n); h.GetUIName(out u); r.Add((h.IsRecommended()==0?"[R] ":"[ ] ") + u + " | " + n); } return r; } }
"@
Add-Type -TypeDefinition $code
[Console]::OutputEncoding = [Text.Encoding]::UTF8
"recommended:"; [AH]::List(".pdf", 1)
"all:"; [AH]::List(".pdf", 0)
