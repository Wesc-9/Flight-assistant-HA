# Flight Assistant — målbildet (27.10.2026)

## Produktregel

**En registrert flybestilling skal være én Home Assistant-enhet.** Bruker legger til ny reise via integrasjonen med **bookingreferanse (PNR) og etternavn**, ikke flynummer. Flyselskapets bestilling må verifiseres før enheten eller reisen bekreftes. PNR er en hemmelig bookingnøkkel: ikke logg den, ikke bruk den i HA-enhetsnavn, device ID, event-bus eller sensorattributter. Ikke lagre etternavn uten nødvendig sikker begrunnelse. Manglende API-tilgang skal meldes tydelig, aldri simuleres.

SAS og Norwegian først. Dersom bestillingen inneholder flere leg (mellomlanding, tur-retur), grupper disse i én reise/enhet, og opprett en sensor for hvert flysegment. Når ny reise registreres skal eksisterende forbli intakt.

## Oppløsning og datakilder

1. **SAS / Norwegian:** PNR + etternavn brukes kun via autorisert oppslag eller lovlig brukerautorisert bestillingsimport. Hent flynummer, avreise/ankomst, planlagt tidspunkt, rutetype, segmenter, flyselskap og personlige bookingopplysninger *der det faktisk er tilgjengelig*. Personlig innsjekkingsstatus kan ikke utledes fra Avinor.
2. **Avinor:** Berik hvert relevant flysegment med gate, innsjekkingsområde, forsinkelser og status. Vis feltnivåkilde og tidspunkt for siste oppdatering; ikke overskriv bekreftet flyselskapsinformasjon med utdatert feed. Cache minst tre minutter.
3. **Flysporing (senere):** Undersøk Flightradar24 / FlightAware og deres faktiske API-vilkår og datakvalitet. Bruk verifisert live GPS-posisjon til kart/framdrift, ikke en animasjon av rutetid som framstilles som sanntid.

## Registreringsskjema – per reise

- Bookingreferanse og etternavn, med flyselskap autodeteksjon der mulig, ellers SAS/Norwegian-valg.
- Resolved innenlands/utenlands, kan overstyres.
- **Ankomstmargin:** innenlands 90 min, utenlands 120 min; slider 45–240 min, trinn 15.
- Har innsjekket bagasje: ja/nei, med advarsel om mulig innsjekkingsfrist og ekstra tid.
- Reiser fra: HA Hjem, HA-sone eller adresseoppslag med bekreftet koordinat (se personvernsamtykke hvis tredjepart brukes).
- Transport: bare bil / bare kollektiv / sammenlign begge.
- Kollektiv sanntid: på/av; på som standard for kollektiv, men vis faktisk kildeoppdatering.
- Valgene må knyttes til *denne* reise-enheten og kunne endres etterpå uten å ødelegge data.

## Tre ankomstforslag

1. **God tid:** ankomst 30 min før brukerens valgte margin.
2. **Anbefalt:** ankomst eksakt etter brukerens valgte margin (90/120 min standard).
3. **Sen avreise:** 30 min senere; **utelates** når konservative sjekk-/bagasjegrenser gjør den for risikabel. Verifiser alltid faktiske flyselskapsfrister før vi kaller et alternativ «trygt».

For hver mulig anbefalt ankomsttid beregn kjøretid inkl. parkering/gangbuffer og/eller en *faktisk* Entur-reise med gåtid/bytter, forsinkelser og ankomst før flyplassfristen. Ikke lag fiktive avganger. Ved endret flytid må varsler holde fast ved gjeldende innsjekkingsfrister, ikke automatisk utsette brukerens avreise.

## Sensorer/dashboard

- Next journey, dag/dato/retning, flight legs, gate, innsjekkingsområde, status og ETA.
- Nedtelling til neste avgang/avreise hjemmefra, oppdatert minst hvert minutt i dashboard.
- Tre forslag på ett kort med kilde og avreisetid; merk forsinkelser/gatebytte.
- Mens flyet er registrert i luften: reell posisjon/kart og ETA bare hvis en faktisk sporingskilde er koblet til. Ellers vis rute og *tydelig merket beregnet fremdrift*, ikke falsk GPS.
- Innstillinger/sensorer må være nyttige på mobil.

## Implementeringsstatus per v0.2.0

- [x] Separate HA-enheter per manuelt registrert fly, nedtellingssensor.
- [x] Per-flyttur lagrede transportvalg, bagasjevalg, avreiselokasjon (adresse for Waze/koordinater for Entur), margin.
- [x] Tre differensierte avreiseforslag med Waze / Entur når API-en gir data.
- [x] Datagrunnlag for «neste reise»-dashboard og Markdown-korteksempel.
- [ ] PNR + etternavn-UI og autorisert ekte flyselskapimport (kritisk blocker).
- [ ] Adresseoppslag som returnerer faktiske koordinater (ikke bare adressetekst).
- [ ] Rediger tur direkte i HA-enhetens innstillinger uten utviklerverktøy.
- [ ] Innsjekkingsfrister per flyselskap, validerte mot ruten.
- [ ] Flersegment i én booking/enhet.
- [ ] Eget polert dashboardkort og verifisert live flykart/ETA.
- [ ] E2E-verifisering i Home Assistant OS og mot reelle tillatte API-kall.

Ikke kall løsningen ferdig før de to første brukerhandlingene fungerer: «Legg til ny reise» med PNR/etternavn, og valg av transport fra en reell adresse.
