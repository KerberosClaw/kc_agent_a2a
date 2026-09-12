#!/usr/bin/env python3
"""Check Markdown repository links and anchors; Mermaid rendering is a separate release check."""
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

root=Path(__file__).resolve().parents[1]
def prose(text):
    return re.sub(r'^```[^\n]*\n.*?^```\s*$', '', text, flags=re.M|re.S)
def anchors(text):
    result=set(); counts={}
    for heading in re.findall(r'^#{1,6}\s+(.+?)\s*#*$',prose(text),re.M):
        heading=re.sub(r'[`*_]','',heading).lower()
        slug=re.sub(r'[^\w\- ]','',heading).replace(' ','-')
        n=counts.get(slug,0); counts[slug]=n+1
        result.add(slug+('-'+str(n) if n else ''))
    return result

def main():
    errors=[]; links=0; diagrams=0
    pages=[p for p in root.rglob('*.md') if not any(x in {'.git','.venv','build','dist'} for x in p.relative_to(root).parts)]
    for path in pages:
        text=path.read_text(); diagrams+=len(re.findall(r'^```mermaid$',text,re.M))
        if len(re.findall(r'^```',text,re.M))%2:errors.append(str(path.relative_to(root))+': unclosed fence')
        for target in re.findall(r'\]\(([^\s)]+)(?:\s+"[^"]*")?\)',prose(text)):
            target=target.strip('<>'); u=urlsplit(target)
            if u.scheme or u.netloc:continue
            dest=(path.parent/unquote(u.path)).resolve() if u.path else path
            links+=1
            if not dest.is_relative_to(root) or not dest.exists():errors.append(str(path.relative_to(root))+': missing target '+target)
            elif u.fragment and dest.suffix=='.md' and unquote(u.fragment) not in anchors(dest.read_text()):
                errors.append(str(path.relative_to(root))+': missing anchor '+target)
    for error in errors:print(error,file=sys.stderr)
    print(f'{len(pages)} Markdown pages, {links} local links, {diagrams} Mermaid blocks, {len(errors)} errors')
    return bool(errors)
if __name__=='__main__':sys.exit(main())
