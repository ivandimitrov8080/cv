import React from 'react';
import { Page, Text, View, Document, StyleSheet } from '@react-pdf/renderer';
import ReactPDF from '@react-pdf/renderer';
import fs from "fs"

const styles = StyleSheet.create({
  page: {
    flexDirection: 'row',
    backgroundColor: '#E4E4E4'
  },
  section: {
    margin: 10,
    padding: 10,
    flexGrow: 1
  }
});

const MyDocument = () => (
  <Document>
    <Page size="A4" style={styles.page}>
      <View style={styles.section}>
        <Text>Section #1</Text>
      </View>
      <View style={styles.section}>
        <Text>Section #2</Text>
      </View>
    </Page>
  </Document>
);

const outDir = process.env.out || "./"
const pname = process.env.pname || "cv"

if (!fs.existsSync(outDir)) {
  fs.mkdirSync(outDir, { recursive: true })
}

ReactPDF.render(<MyDocument />, `${outDir}/${pname}.pdf`);

