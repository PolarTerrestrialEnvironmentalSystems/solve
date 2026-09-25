"""Select short original source passages; models refer to IDs, never invent text."""
import re


def passages(parsed, wb, topics, cap, core):
    candidates=[]
    seen=set()
    for number, fragment in enumerate(parsed.get('fragments',[])):
        text=fragment['text']
        matches=[]
        for name in core.names(wb):
            pattern=r'(?<!\w)'+r'\s+'.join(re.escape(x) for x in name.split())+r'(?!\w)'
            matches.extend(re.finditer(pattern,text,re.I))
        for match in sorted(matches,key=lambda m:m.start()):
            start=max(0,match.start()-240)
            end=min(len(text),match.end()+540)
            if start:
                start=text.rfind(' ',0,start)+1
            if end<len(text):
                space=text.find(' ',end)
                end=space if space>=0 else len(text)
            quote=text[start:end]
            if not core.identity_matches(wb,quote):
                continue
            key=(fragment['locator'],core.norm(quote))
            if key in seen:
                continue
            seen.add(key)
            score=3+len(core.topic_hits(quote,topics))
            candidates.append((score,number,start,dict(locator=fragment['locator'],text=quote,offset=start)))
        if number in {0,len(parsed.get('fragments',[]))-1} and text.strip():
            candidates.append((0,number,0,dict(locator=fragment['locator'],text=text[:1000],offset=0)))
    candidates.sort(key=lambda x:(-x[0],x[1],x[2]))
    out=[];used=0
    for _,_,_,fragment in candidates:
        if used+len(fragment['text'])>cap:
            continue
        # Do not fill the input with overlapping windows around the same name occurrence.
        if any(x['locator']==fragment['locator'] and abs(x['offset']-fragment['offset'])<300 for x in out):
            continue
        out.append(dict(fragment,evidence_id='e'+str(len(out)+1)))
        used+=len(fragment['text'])
        if len(out)>=30:
            break
    return out
