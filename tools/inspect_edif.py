"""Quick structural dump of an OrCAD Capture EDIF 2.0.0 export.

usage: python tools/inspect_edif.py MYBOARD.EDF [cell-name]

Prints: libraries, page list with element counts, orientation histogram,
figure primitive histogram, and (optionally) a pretty-printed cell.
"""
import sys, os
from collections import Counter
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from orcad2kicad.sexp import parse, head, child, children, walk, edif_name, edif_id


def pp(n, d=0, maxd=8, maxc=40):
    if not isinstance(n, list):
        return
    print('  ' * d + str(head(n)), [c for c in n[1:] if not isinstance(c, list)][:5])
    if d < maxd:
        for c in n[1:maxc]:
            pp(c, d + 1, maxd, maxc)


def main(path, cell_name=None):
    if cell_name == '--model':
        from orcad2kicad.edif_reader import load_edif
        d = load_edif(path)
        for pg in d.pages:
            print(pg.name, 'inst', len(pg.instances), 'wires', len(pg.wires), 'junc', len(pg.junctions),
                  'labels', len(pg.labels), 'pwr', len(pg.power_ports), 'off', len(pg.offpages), 'nets', len(pg.nets))
        print('symbols', len(d.symbols), 'issues', len(d.issues))
        for i in d.issues[:30]:
            print('  ', i)
        return
    text = open(path, encoding='latin-1').read()
    tree = parse(text, escape=False)[0]   # EDIF는 백슬래시 이스케이프 규약이 없음(리터럴 그대로, sexp.py 참조)
    libs = children(tree, 'library')
    print('libraries:', len(libs))
    for l in libs:
        print('  ', edif_name(l[1]), 'cells:', len(children(l, 'cell')))
    design_lib = libs[-1]
    root = [c for c in children(design_lib, 'cell') if child(c, 'view') and child(child(c, 'view'), 'contents')
            and children(child(child(c, 'view'), 'contents'), 'page')]
    if root:
        cont = child(child(root[0], 'view'), 'contents')
        print('root cell:', edif_name(root[0][1]))
        print('offPageConnector ports:', len(children(cont, 'offPageConnector')))
        for pg in children(cont, 'page'):
            print('page', edif_name(pg[1]), dict(Counter(str(head(c)) for c in pg[1:])))
    print('orientations', Counter(str(child(t, 'orientation')[1]) if child(t, 'orientation') else 'R0'
                                  for t in walk(tree, 'transform')))
    print('figure groups', Counter(str(f[1]) for f in walk(tree, 'figure') if not isinstance(f[1], list)))
    print('primitives', Counter(str(head(x)) for f in walk(tree, 'figure') for x in f[1:]
                                if isinstance(x, list) and head(x) in ('path', 'rectangle', 'circle', 'polygon', 'openShape', 'shape', 'dot')))
    if cell_name:
        for l in libs:
            for c in children(l, 'cell'):
                if edif_name(c[1]) == cell_name or edif_id(c[1]) == cell_name:
                    pp(c)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
