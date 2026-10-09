RENDERBATCH 3.9.0
===============
Procesare în lot pentru randări: reducere zgomot, gradare de ambianță / reiluminare, potrivire culori, mărire rezoluție, claritate.

NOU ÎN 3.9 - TOTUL SE DESCARCĂ SINGUR
  Installerul e tot ce-ți trebuie: pachetele Python și modelele (circa 250 MB) se descarcă din depozitul
    RenderBatch (github.com/RewZes/image-batch-editing) și se verifică înainte de despachetare. Dacă RenderBatch
    e deja pe PC, ți se propune întâi copierea din el (mai rapid, păstrează setările).
  Modele implicite: SCUNet (fidel / GAN), SPAN 2x NomosUni și 4x-UltraSharp. La prima pornire aplicația le
    descarcă pe cele lipsă de pe site-urile autorilor și le convertește (convertorul se descarcă o dată, circa
    120 MB). Dacă un link nu merge, primești o listă cu pagina fiecărui model și folderul în care să-l pui.
  Listele Reducere zgomot / Mărire arată mereu modelele implicite, cu eticheta „implicit”. Cele nedescărcate au
    o iconiță de descărcare: alege unul și se descarcă (progresul apare în listă).
  4x-UltraSharp e listat de autor ca necomercial: verifică-i pagina înainte să-l folosești pentru clienți.

NOU ÎN 3.8 - INSTALLER ȘI ACTUALIZĂRI CU UN CLIC
  RenderBatch_Setup_3.8.0.exe: dublu clic, apoi alegi
    Instalează pe acest PC - în folderul tău de utilizator (fără drepturi de administrator), scurtături în
      meniul Start și pe desktop, dezinstalare din Setări Windows > Aplicații;
    Portabil - totul într-un folder ales de tine (și pe un stick USB), nimic scris în afara lui.
  Installerul ia pachetele Python și modelele din fișierele RenderBatch_*.rbdata de lângă el (sau din
    Descărcări) sau le copiază, împreună cu setările, presetările și aspectele tale, dintr-un folder
    RenderBatch care e deja pe PC - nimic de descărcat din nou.
  Dezinstalare: Setări Windows > Aplicații > RenderBatch (sau Uninstall.exe din folder). Te întreabă dacă
    păstrezi setările, presetările, aspectele, LUT-urile și modelele.
  Actualizări: Setări > Actualizări > Instalează actualizarea…, sau trage .zip-ul actualizării în fereastră,
    sau lasă-l în Descărcări - RenderBatch ți-l propune la pornire, îl instalează și repornește. Fișierele
    despachetate de aplicație nu primesc avertismentul Windows „descărcat de pe internet”. Dezarhivarea
    manuală a zip-ului merge în continuare.

NOU ÎN 3.7
  3.7.3: la schimbarea limbii din Setări se traduc acum și titlurile de carduri cu „&” (Textură și
    granulație) și sfaturile compuse din mai multe părți.
  3.7.2: un clic într-o casetă numerică selectează tot numărul (fără unitatea de după, ca „×”), ca să poți
    scrie direct valoarea nouă. LightMix răspunde mult mai repede: la schimbarea unei lumini se adaugă doar
    diferența ei, nu se amestecă din nou toate pasele, iar maparea tonală Corona rulează pe toate nucleele
    procesorului (imagine 4K cu 20 de lumini: circa 0,5 s în loc de 4-10 s).
  Fișierele .cxr se deschid de aproximativ două ori mai repede (pasele de lumină se citesc de 4-5 ori
    mai repede).
  Butonul Aspecte / Presetări arată ce ai ales; cu previzualizarea sau banda de miniaturi selectată,
    săgețile sus / jos trec la aspectul (sau presetarea) anterior / următor.
  „Resetează tot” și insigna plăcii video s-au mutat în bara de jos, în dreapta „Jurnal”.
  Meniurile Aspecte / Presetări încep cu „Niciunul”. Clic dreapta pe Aspecte: fără aspect (doar setările
    de aspect revin la implicit). Clic dreapta pe Presetări: toate setările revin la implicit.

NOU ÎN 3.6 - ASPECTE
  LUT: cu două sau mai multe fișiere .cube în folderul luts (și subfoldere), câmpul LUT devine o listă
    derulantă; Răsfoiește alege în continuare orice alt fișier.
  Două butoane în dreapta sus, Aspecte și Presetări:
    Aspectele schimbă doar cum arată imaginea: glisoarele de ambianță, LUT, claritate / granulație și
      Atmosfera. Un aspect nou îl înlocuiește pe cel anterior, dar păstrează reducerea zgomotului, mărirea,
      claritatea (sharpen), ieșirea și folderele.
    Presetările păstrează toate setările.
  13 aspecte incluse: cinematic (turcoaz și portocaliu, dramă caldă), film retro anii '70, fotografie
    profesională, ora de aur perfectă, noapte senină, scandinav luminos, interior luxos cald, întunecat și
    dramatic, alb-negru artistic, dimineață pastel, film cald, lumină nordică rece (ține mouse-ul pe unul
    în meniu pentru descriere). Fiecare are propria corecție de culoare (LUT); o poți ajusta apoi cu
    glisoarele.
  „Salvează aspectul…” salvează în folderul looks, „Salvează presetarea…” în folderul presets; ambele apar
    în meniu.

NOU ÎN 3.5
  Cardul Rezoluție / mărire:
    „Mărește de mai multe ori” + un număr: mărirea rulează de atâtea ori la rând, de fiecare dată cu
      factorul de scalare (2x de trei ori = 8x), de ex. o textură de tapet pentru tipar. Numele
      fișierului primește _upscaled_x<ori>. Doar cu „Factor de scalare”. Rezultatele prea mari pentru
      memoria PC-ului sunt refuzate.
    „Salvează și o versiune nemărită”: salvează <nume>_final_no-upscale lângă rezultat, iar fișierul
      mărit primește numele modelului (<nume>_final_<model>), ca să poți compara modelele de mărire.
  Presetări: butonul Presetări arată toate presetările din folderul presets al aplicației; clic pentru a
    folosi una. „Salvează presetarea…” cere doar un nume. Copiază presetări .json în acel folder pentru a
    le împărtăși.

NOU ÎN 3.4
  Schimbarea limbii (Setări) se face pe loc: nimic nu se reîncarcă, previzualizarea, zoom-ul, regiunea și
    un lot în desfășurare rămân la fel, iar fereastra Setări își schimbă limba cât e deschisă.
  Rotița mouse-ului derulează banda de miniaturi stânga / dreapta.

NOU ÎN 3.3 - MAI RAPID
  Lot: cât timp placa video lucrează la o imagine, următoarea e deja citită, iar cea anterioară e
    finalizată și salvată; corecția de culoare / ceața / salvarea folosesc toate firele CPU din Setări.
  Miniaturile sunt păstrate într-un cache mic (cache\thumbs, se poate șterge), deci folderele se
    redeschid instant; cele noi se fac câte mai multe deodată.
  Imaginile vecine sunt citite din timp, deci săgețile / clicul pe bandă schimbă imaginea aproape
    instant. Pornire mai rapidă și actualizări mai fluide ale previzualizării.
  Sfat: Setări > limita de utilizare GPU la 100% face loturile cele mai rapide (mai jos = laptop mai rece).

NOU ÎN 3.2 - ATMOSFERĂ (card nou sub „Ambianță / reiluminare”)
  Momentul zilei: Ca în randare / Zi / Amurg / Noapte. Fișierele .cxr sunt reiluminate prin pasele
    LightMix (lumini numite Sun..., Environment / Sky / HDRI..., restul contează ca lumini interioare);
    celelalte imagini cu o corecție care păstrează lămpile aprinse. Intensitate, Lumini interioare,
    Cer și soare, Căldura luminilor - reglaj fin.
  Ceață / pâclă: Densitate, Începe de la, Ceață la sol, Strălucire în jurul luminilor, Culoarea ceții
    (pătrat alb = automat).
  Raze de lumină: Intensitate, Lungime, Lumina vine din (unghi), Împrăștiere (fascicule paralele de
    soare ... evantai dintr-o lampă), Doar din zone mai luminoase de; „Alege pe imagine” stabilește
    punctul din care vin razele. Pentru .cxr, „Raze de la” poate fi o lumină LightMix (de ex. Sun).
  Ceața și razele au nevoie de adâncime: pasa Corona ZDepth (CGeometry_ZDepth) din .cxr se folosește
    dacă există, altfel modelul AI de adâncime (Depth Anything V2 Small, 99 MB, gratuit): apasă
    „Descarcă” în card o dată sau copiază manual depth_anything_v2_small.onnx în models\depth.
    Fără ele se folosește o estimare aproximativă.

NOU ÎN 3.1
  Regiune (R): desenează un chenar pe previzualizare; reducerea zgomotului / mărirea / claritatea /
    Verificare detalii rulează doar în interiorul lui, la orice zoom. Margini - mărime, interior -
    mutare, x - ștergere.
  Butonul Anulează de lângă bara de progres oprește tot ce rulează.
  Rezoluție: implicit „Factor de scalare”; câmpurile gri arată valorile rezultate.
  Fiecare câmp numeric are săgeți: clic, ținut apăsat sau tras sus / jos; clic dreapta resetează.

NOU ÎN 3.0
  Limba se schimbă imediat, fără repornire.
  Trage imagini / .cxr pe previzualizare (sau pe banda de miniaturi): sunt copiate în folderul de intrare.
  Trage o imagine pe „Potrivire culori” pentru a o folosi ca referință.
  Banda de miniaturi: trage pentru reordonare (Ctrl / Shift pentru mai multe deodată);
    „Resetează ordinea” revine la ordinea automată. Lotul se procesează în ordinea benzii.
  „Procesează selecția” lângă „Procesează folderul” procesează doar miniaturile selectate.
  Fișierele .cxr deja deschise se redeschid instantaneu (pasele de lumină rămân în memorie).

PORNIRE
  Dublu-clic pe RenderBatch.exe. Nu trebuie instalat nimic. Windows 10 (1809+) sau Windows 11.
  Păstrează toate folderele (runtime, app, models, luts) lângă RenderBatch.exe.

LIMBĂ
  Setări > Limbă: English, Русский, Română. Aplicația repornește pentru a schimba limba.

FEREASTRA
  Stânga: setări în carduri. Clic pe titlul unui card pentru a-l restrânge. Comutatorul din dreapta
           cardului activează sau dezactivează acel pas (Ambianță, Potrivire culori, Reducere zgomot,
           Rezoluție / mărire, Claritate).
           Panoul Lot este fixat jos: Procesează / Pauză / Anulează, progresul și timpul rămas.
  Centru:  previzualizarea. Alege imaginea din bara de instrumente, din banda de imagini sau cu săgețile.
  Banda de imagini: imaginile deja procesate (găsite în folderul de ieșire) apar primele, cu chenar
           verde; cea în procesare are chenar portocaliu; roșu înseamnă că a eșuat (vezi jurnalul).
  Sus:     „Resetează tot” readuce toate setările la valorile implicite (folderele se păstrează) -
           primești un buton Anulează.
  Jos:     bara de activitate arată ce rulează, cu o bară de progres. „Jurnal de activitate” deschide jurnalul.

PREVIZUALIZARE
  Actualizează previzualizarea (butonul din bara de instrumente sau F5) aplică modificările în așteptare
  și randează fiecare pas activ (inclusiv reducerea zgomotului și mărirea) pe zona mărită.
  Butonul se aprinde când există modificări în așteptare.

  Reducere zgomot și Rezoluție / mărire au fiecare o opțiune „Previzualizare” în cardul lor:
    Interactiv   AI-ul randează zona mărită doar când muți, mărești sau redimensionezi vizualizarea
    Manual       nu rulează nimic până nu apeși Actualizează previzualizarea sau Verificare detalii (implicit)
  Zona cu zgomot redus / mărită rămâne pe ecran cât timp modifici setările rapide (ambianță, expunere,
  claritate, claritate locală, granulație...): doar acestea sunt reaplicate peste ea, într-o clipă. Este
  înlocuită doar când muți/mărești/redimensionezi vizualizarea sau apeși Actualizează previzualizarea.
  Modificarea unei setări de reducere zgomot / mărire păstrează vechiul rezultat pe ecran și aprinde
  Actualizează previzualizarea.
  Ambianță, potrivire culori și claritate: Setări > „Ambianță, potrivire culori și claritate”
    Automat      previzualizarea urmează fiecare mișcare a glisoarelor (implicit)
    Manual       modificările așteaptă Actualizează previzualizarea
  Eticheta „după” arată ce pași sunt randați pe ecran, ex. „DUPĂ · +MĂRIRE +CLARITATE”.
  Reducerea zgomotului / mărirea / claritatea lucrează pe detalii fine, deci apar când mărești.

  Rotiță: zoom la mouse · Tragere: deplasare · Trage mânerul rotund: mută linia de divizare
  Dublu-clic: încadrare / 100% · F: încadrare · 1: 100%
  Mărirea în vizualizarea Divizat trece la două panouri sincronizate, înainte și după.

SETĂRI BUNE DE ȘTIUT
  Rotița mouse-ului nu schimbă niciodată din greșeală o listă, o casetă numerică sau un glisor: peste ele
  doar derulează panoul. Dă mai întâi clic pe unul dacă vrei să folosești rotița pe el.
  Numere: glisorul acoperă intervalul uzual; dă clic în caseta numerică pentru a introduce orice valoare
  dincolo de el (ține mouse-ul peste casetă pentru a vedea limitele).
  Potrivire culori: „Alege orice imagine...” îți permite să folosești o referință care nu e în folderul lotului.
  „Folosește mărirea AI doar la o mărire de cel puțin”: ex. 1.30x înseamnă că o imagine mărită de 1,3 ori
  sau mai mult trece prin mărirea AI; schimbările mai mici folosesc redimensionare normală.
  Claritate „Protejează zonele netede”: detaliile mai slabe de atât nu sunt accentuate, deci granulația
  rămâne calmă.

TEXTURĂ ȘI GRANULAȚIE (activează comutatorul cardului pentru a o folosi)
  Claritate       readuce contrastul local (profunzimea) aplatizat de reducerea zgomotului și mărire.
                  Se actualizează live la orice zoom.
  Granulație      granulație fotografică ce urmează lumina, ca la un aparat foto real:
                    Senzor digital: cea mai multă în umbre și tonuri medii, deloc în zonele arse
                    Film: cea mai multă în tonurile medii, scade spre negru și alb
                  Mărimea este în pixeli ai imaginii finale; Culoare amestecă puncte mono și colorate.
                  Fiecare fișier primește propriul model de granulație, același la fiecare procesare,
                  deci loturile sunt uniforme și rulările repetate identice. Mărește la 100% pentru a o
                  evalua: previzualizarea mărită arată exact granulația pe care o va produce lotul.
  Sursă granulație  granulația integrată (recomandat) sau un model 1x din models\grain.

FIȘIERE CORONA .CXR - LIGHTMIX
  Pune randările Corona .cxr în folderul de intrare (le poți amesteca cu PNG/JPG/TIF).
  Fiecare .cxr este afișat exact cum îl arată Corona: LightMix-ul și setările Post (expunere, balans de
  alb, nuanță, contrast, saturație, compresia luminilor, ACES, filmic, vignetă, curbe, LUT) sunt citite
  din fișier și reproduse (verificat față de Corona Image Editor, cu diferențe mult sub 1/255).
  Înainte = fișierul așa cum a fost salvat în Corona.  După = modificările luminilor + tot restul din aplicație.

  Butonul „💡 LightMix” (bara de instrumente) apare când folderul are fișiere .cxr; deschide panoul din dreapta.
    Lumini individuale / Grupuri de lumini   comutator în partea de sus a panoului
    Fiecare lumină:  bifă = pornit/oprit · Intensitate × (înmulțește valoarea salvată în fiecare fișier) ·
                 caseta de culoare
    Caseta de culoare: clic = selector de culoare cu glisor de temperatură de culoare · trage-o pe caseta
                 de culoare a altei lumini = copiază culoarea · clic dreapta = revine la culoarea salvată
                 în fișier („fișier”)
    O modificare se aplică luminii cu acel nume în FIECARE fișier, astfel întregul lot rămâne uniform.
  Lumini individuale și Grupuri de lumini sunt două configurații separate, cu valori proprii; cea selectată
  este cea pe care o vezi și cea care se procesează.
  Culori: o casetă albă = culoarea salvată în fiecare fișier. Selectorul de culoare are glisoare pentru
    temperatură, nuanță, saturație și luminozitate, hex / RGB și culorile deja folosite; imaginea se
    actualizează în timp ce alegi (Anulează pune la loc culoarea veche).
  Grupuri: dublu-clic pe numele unui grup pentru a-l redenumi.
  Grupuri (ex. unul pe cameră): „+ Creează grup” în partea de jos a panoului, dă-i un nume și bifează-i
    luminile, sau trage o lumină de mânerul ⠿ pe un grup. Un grup are propriul pornit/oprit, intensitate
    (înmulțește luminile lui) și culoare (o înlocuiește pe a lor). În „Grupuri de lumini” luminile grupate
    sunt ascunse din listă; „Lumini individuale” arată fiecare lumină (etichetată cu grupul ei). Grupurile
    rămân după Resetează tot.
    ⋯ pe un grup: redenumește, schimbă-i luminile sau Degrupează.
  Folosește setările Post din fiecare fișier: dezactivat = LightMix simplu, fără tone mapping-ul Corona.
  Pase de lumină: Ca la salvare / Întotdeauna fără zgomot / Niciodată (brute).
  Salvează… / Încarcă… păstrează o configurație de iluminare cu grupurile ei (ex. „Seară”) pentru a o
  refolosi în alte proiecte.

  LUT-uri folosite în Post-ul Corona: pune fișierul .cube lângă fișierele .cxr sau în folderul luts al aplicației.
  Nu se reproduc: bloom & glare din Corona. Expunerea fotografică este aproximată.
  Pot fi citite doar fișierele .cxr salvate cu compresie ZIP (implicit în Corona) sau fără compresie.

AFIȘEAZĂ: IMAGINI / CXR (dreapta jos)
  Debifează una pentru a ascunde acele fișiere din banda de imagini ȘI din lot (ex. procesezi doar .cxr).
  Ascunderea CXR ascunde și panoul LightMix; luminile și grupurile lui se păstrează.

MĂRIMEA INTERFEȚEI
  Setări > Mărimea interfeței: Compact (implicit) sau Confortabil (mai mare). Spațierea se actualizează
  complet după o repornire.

BARA LATERALĂ ȘI IEȘIREA
  Trage o secțiune de titlu pentru a o muta în sus sau în jos. Setări > Bară laterală afișează / ascunde secțiuni.
  Ordinea și vizibilitatea sunt reținute, și după Resetează tot.
  Formatul de ieșire, biții și sufixul numelui sunt în Setări > Ieșire. 32 de biți = TIFF în virgulă mobilă.
  „Omite imaginile deja procesate” este în panoul Lot.
  Glisoare: dă clic oriunde pe un glisor pentru a sări acolo (și continuă să tragi).

ERORI
  Când ceva nu e în regulă, setarea care cauzează problema primește un contur roșu, cardul ei se deschide
  și mesajul este afișat chiar acolo. Corecteaz-o și conturul dispare.

MODELE
  models\denoise   modele de reducere zgomot (1x)
  models\upscale   modele de mărire rezoluție (2x, 4x ...)
  models\grain     modele opționale de granulație / textură (1x)
  Fiecare listă afișează doar propriul folder.

  Pune modele în oricare dintre aceste formate în acele foldere, chiar și cu aplicația deschisă:
    .onnx                      folosite ca atare
    .pth .safetensors .pt .ckpt   convertite automat (bara de activitate arată progresul, de obicei
                               sub un minut). Copia convertită este verificată față de original;
                               abia apoi originalul este șters.
  Un model pus în folderul greșit (ex. un model de mărire 4x în denoise) este mutat în cel corect.
  Dacă un fișier nu poate fi convertit (descărcare defectă sau nu e model de reducere zgomot / mărire),
  este lăsat neatins, primești un mesaj și nu se reîncearcă decât dacă fișierul se schimbă.
  Sursă bună: openmodeldb.info

SETĂRI (dreapta sus)
  Teme, limită de utilizare GPU (menține PC-ul receptiv în timpul unui lot), fire CPU, calitate
  previzualizare, AI live și butoane care deschid folderele de modele / LUT.

DACĂ CEVA NU MERGE
  Dacă aplicația afișează o eroare la pornire, scrie crash.log în acest folder. Trimite acel fișier
  pentru ajutor.
