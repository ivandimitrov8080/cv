{-# LANGUAGE DeriveGeneric #-}
{-# LANGUAGE OverloadedStrings #-}

-------------------------------------------------------------------------------
-- Config Section: fonts, colors, layouts, style options
-------------------------------------------------------------------------------
module Main where

import Control.Monad (foldM)
import Data.Aeson (FromJSON (..), Value, defaultOptions, eitherDecode, fieldLabelModifier, genericParseJSON)
import Data.Aeson.Types (Parser)
import Data.ByteString.Lazy qualified as BSL
import Data.Maybe (fromMaybe)
import Data.Text qualified as T
import GHC.Generics (Generic)
import Graphics.PDF
import Graphics.PDF.Fonts.StandardFont

-- HPDF library import
data FontConfig = FontConfig
  { fontHeader :: String,
    fontBody :: String,
    sizeHeader :: Double,
    sizeBody :: Double
  }
  deriving (Show, Generic)

instance FromJSON FontConfig

-- Only black on white, but allow for possible future extension.
data ColorConfig = ColorConfig
  { fgColor :: String, -- e.g., "#000000"
    bgColor :: String, -- e.g., "#FFFFFF"
    accentColor :: String -- for lines etc.
  }
  deriving (Show, Generic)

instance FromJSON ColorConfig

data LayoutConfig = LayoutConfig
  { marginTop :: Double,
    marginBottom :: Double,
    marginLeft :: Double,
    marginRight :: Double,
    sectionSpacing :: Double
  }
  deriving (Show, Generic)

instance FromJSON LayoutConfig

-- Top-level config embodies all UI-style choices
data CVConfig = CVConfig
  { font :: Maybe FontConfig,
    color :: Maybe ColorConfig,
    layout :: Maybe LayoutConfig
  }
  deriving (Show, Generic)

instance FromJSON CVConfig

defaultFontConfig :: FontConfig
defaultFontConfig = FontConfig "Helvetica-Bold" "Helvetica" 18 11

defaultColorConfig :: ColorConfig
defaultColorConfig = ColorConfig "#000000" "#FFFFFF" "#505050"

defaultLayoutConfig :: LayoutConfig
defaultLayoutConfig = LayoutConfig 36 36 36 36 18 -- points

-------------------------------------------------------------------------------
-- CV Section: data types corresponding to sections in the CV
-------------------------------------------------------------------------------
data Link = Link
  { linkText :: String,
    linkHref :: String
  }
  deriving (Show, Generic)

instance FromJSON Link where
  parseJSON :: Value -> Parser Link
  parseJSON = genericParseJSON defaultOptions {fieldLabelModifier = \k -> case k of "linkText" -> "text"; "linkHref" -> "href"; x -> x}

-- Allow (Maybe) for extra/optional fields in PersonalInfo
-- Added description, title, github, website, upwork as Maybe fields
-- All as per your file structure

data PersonalInfo = PersonalInfo
  { piName :: String,
    piDescription :: Maybe String,
    piTitle :: Maybe String,
    piEmail :: Maybe String,
    piGithub :: Maybe String,
    piWebsite :: Maybe String,
    piUpwork :: Maybe String
  }
  deriving (Show, Generic)

instance FromJSON PersonalInfo where
  parseJSON :: Value -> Parser PersonalInfo
  parseJSON =
    genericParseJSON
      defaultOptions
        { fieldLabelModifier = \k -> case k of
            "piName" -> "name"
            "piDescription" -> "description"
            "piTitle" -> "title"
            "piEmail" -> "email"
            "piGithub" -> "github"
            "piWebsite" -> "website"
            "piUpwork" -> "upwork"
            x -> x
        }

data Experience = Experience
  { expCompany :: String,
    expPosition :: Maybe String,
    expLocation :: Maybe String,
    expFrom :: Maybe String,
    expTo :: Maybe String,
    expDescription :: Maybe String,
    expLinks :: Maybe [Link],
    expFeedback :: Maybe String
  }
  deriving (Show, Generic)

instance FromJSON Experience where
  parseJSON :: Value -> Parser Experience
  parseJSON =
    genericParseJSON
      defaultOptions
        { fieldLabelModifier = \k -> case k of
            "expCompany" -> "company"
            "expPosition" -> "position"
            "expLocation" -> "location"
            "expFrom" -> "from"
            "expTo" -> "to"
            "expDescription" -> "description"
            "expLinks" -> "links"
            "expFeedback" -> "feedback"
            x -> x
        }

data Education = Education
  { eduInstitution :: String,
    eduLocation :: Maybe String,
    eduDegree :: Maybe String,
    eduField :: Maybe String,
    eduFrom :: Maybe String,
    eduTo :: Maybe String,
    eduSummary :: Maybe String
  }
  deriving (Show, Generic)

instance FromJSON Education where
  parseJSON :: Value -> Parser Education
  parseJSON =
    genericParseJSON
      defaultOptions
        { fieldLabelModifier = \k -> case k of
            "eduInstitution" -> "institution"
            "eduLocation" -> "location"
            "eduDegree" -> "degree"
            "eduField" -> "field"
            "eduFrom" -> "from"
            "eduTo" -> "to"
            "eduSummary" -> "summary"
            x -> x
        }

data Skill = Skill
  { skillCategory :: String,
    skillItems :: [String]
  }
  deriving (Show, Generic)

instance FromJSON Skill where
  parseJSON :: Value -> Parser Skill
  parseJSON =
    genericParseJSON
      defaultOptions
        { fieldLabelModifier = \k -> case k of
            "skillCategory" -> "category"
            "skillItems" -> "items"
            x -> x
        }

data Certificate = Certificate
  { certName :: String,
    certIssuer :: Maybe String,
    certDescription :: Maybe String,
    certDate :: Maybe String,
    certLinks :: Maybe [Link]
  }
  deriving (Show, Generic)

instance FromJSON Certificate where
  parseJSON :: Value -> Parser Certificate
  parseJSON =
    genericParseJSON
      defaultOptions
        { fieldLabelModifier = \k -> case k of
            "certName" -> "name"
            "certIssuer" -> "issuer"
            "certDescription" -> "description"
            "certDate" -> "date"
            "certLinks" -> "links"
            x -> x
        }

-- The main CV type; make most fields optional, and support certificates
-- Allow missing fields to not break parsing (by using Maybe or [])
data CV = CV
  { cvPersonalInfo :: PersonalInfo,
    cvExperience :: [Experience],
    cvEducation :: [Education],
    cvCertificates :: Maybe [Certificate],
    cvSkills :: Maybe [Skill],
    cvConfig :: Maybe CVConfig
  }
  deriving (Show, Generic)

instance FromJSON CV where
  parseJSON :: Value -> Parser CV
  parseJSON =
    genericParseJSON
      defaultOptions
        { fieldLabelModifier = \k -> case k of
            "cvPersonalInfo" -> "personalInfo"
            "cvExperience" -> "experience"
            "cvEducation" -> "education"
            "cvCertificates" -> "certificates"
            "cvSkills" -> "skills"
            "cvConfig" -> "config"
            x -> x
        }

-------------------------------------------------------------------------------
-- Render Section: rendering logic for transforming CV -> PDF
-------------------------------------------------------------------------------

-- Render CV as monadic PDF (one classic black-on-white page)
renderCV :: AnyFont -> AnyFont -> CV -> PDF ()
renderCV fontHeader fontBody cv = do
  page <- addPage Nothing
  drawWithPage page (renderCVPage (PDFFont fontHeader 18) (PDFFont fontBody 11) cv)

-- REMOVED BROKEN addFont

-- All y coordinates start high and reduce as we print sections
type Cursor = PDFFloat

startY :: PDFFloat
startY = 770

renderCVPage :: PDFFont -> PDFFont -> CV -> Draw ()
renderCVPage fontHeader fontBody cv = do
  let personal = cvPersonalInfo cv
      experience = cvExperience cv
      education = cvEducation cv
      certificates = cvCertificates cv
      skills = cvSkills cv
  y1 <- drawPersonalInfo fontHeader personal startY
  y2 <- drawSection fontHeader fontBody "Experience" y1
  y3 <- foldM (drawExperience fontBody) (y2 - 20) experience
  y4 <- drawSection fontHeader fontBody "Education" (y3 - 30)
  y5 <- foldM (drawEducation fontBody) (y4 - 20) education
  y6 <- case certificates of
    Just certs -> do
      ycert <- drawSection fontHeader fontBody "Certificates" (y5 - 30)
      foldM (drawCertificate fontBody) (ycert - 20) certs
    Nothing -> return y5
  _y7 <- case skills of
    Just sks -> do
      yskill <- drawSection fontHeader fontBody "Skills" (y6 - 30)
      foldM (drawSkill fontBody) (yskill - 20) sks
    Nothing -> return y6
  return ()

-- Section header at (x, y)
drawSection :: PDFFont -> PDFFont -> String -> Cursor -> Draw Cursor
drawSection fontHeader _ s y = do
  drawText $ do
    setFont fontHeader
    text fontHeader 72 y (T.pack s)
  return (y - 18)

-- Draw personal info and return next Y
-- Big name, small title/email/subs
-- Use fontHeader for name, fontBody for rest
-- Returns new y cursor

drawPersonalInfo :: PDFFont -> PersonalInfo -> Cursor -> Draw Cursor
drawPersonalInfo fontHeader pi y = do
  drawText $ do
    setFont fontHeader
    text fontHeader 72 y (T.pack (piName pi))
  let linesAll =
        [ maybe "" id (piTitle pi),
          maybe "" id (piDescription pi),
          maybe "" id (piEmail pi),
          "github: " ++ maybe "" id (piGithub pi),
          "website: " ++ maybe "" id (piWebsite pi),
          "upwork: " ++ maybe "" id (piUpwork pi)
        ]
      nextLines = filter (/= "") linesAll
      lineStep = 16
      y' = y - 32
  foldM
    ( \yc l -> do
        drawText $ do
          setFont fontHeader
          text fontHeader 72 yc (T.pack l)
        return (yc - lineStep)
    )
    y'
    nextLines

-- Draw an experience section block, returns new Y position
-- One job per call
drawExperience :: PDFFont -> Cursor -> Experience -> Draw Cursor
drawExperience font y e = do
  drawText $ do
    setFont font
    text
      font
      72
      y
      ( T.pack
          ( expCompany e
              ++ maybe "" (" / " ++) (expPosition e)
              ++ maybe "" (" — " ++) (expLocation e)
          )
      )
  let y1 = y - 14
  drawText $ text font 82 y1 (T.pack (maybe "" id (expFrom e) ++ " - " ++ maybe "" id (expTo e)))
  let y2 = y1 - 14
  case expDescription e of
    Just desc | not (null desc) -> drawText (text font 82 y2 (T.pack desc)) >> return (y2 - 14)
    _ -> return y2

drawEducation :: PDFFont -> Cursor -> Education -> Draw Cursor
drawEducation font y e = do
  drawText $ do
    setFont font
    text
      font
      72
      y
      ( T.pack
          ( eduInstitution e
              ++ maybe "" (", " ++) (eduDegree e)
              ++ maybe "" (", " ++) (eduField e)
          )
      )
  drawText $ text font 82 (y - 14) (T.pack (maybe "" id (eduFrom e) ++ " - " ++ maybe "" id (eduTo e)))
  case eduSummary e of
    Just summary | not (null summary) -> drawText (text font 82 (y - 28) (T.pack summary)) >> return (y - 42)
    _ -> return (y - 28)

drawCertificate :: PDFFont -> Cursor -> Certificate -> Draw Cursor
drawCertificate font y c = do
  drawText $ do
    setFont font
    text font 72 y (T.pack (certName c ++ maybe "" ((" (" ++) . (++ ")")) (certIssuer c)))
  drawText $ text font 82 (y - 14) (T.pack (maybe "" id (certDate c)))
  case certDescription c of
    Just desc | not (null desc) -> drawText (text font 82 (y - 28) (T.pack desc)) >> return (y - 42)
    _ -> return (y - 28)

drawSkill :: PDFFont -> Cursor -> Skill -> Draw Cursor
drawSkill font y s = do
  drawText $ do
    setFont font
    text font 72 y (T.pack (skillCategory s ++ ": " ++ unwords (skillItems s)))
  return (y - 14)

-------------------------------------------------------------------------------
-- Program Section: read JSON, parse and drive the rendering (main IO)
-------------------------------------------------------------------------------

main :: IO ()
main = do
  fontHeaderResult <- mkStdFont Times_Bold
  fontBodyResult <- mkStdFont Times_Roman
  let fontHeader = case fontHeaderResult of
        Right fh -> fh
        Left e -> error ("Header font error: " ++ show e)
      fontBody = case fontBodyResult of
        Right fb -> fb
        Left e -> error ("Body font error: " ++ show e)
  jsonData <- BSL.readFile "cv.json"
  let parseResult = eitherDecode jsonData :: Either String CV
  case parseResult of
    Left err -> putStrLn $ "Error parsing cv.json: " ++ err
    Right cv -> do
      putStrLn "Successfully parsed cv.json! Generating cv.pdf..."
      runPdf "out/cv.pdf" standardDocInfo (PDFRect 0 0 595 842) (renderCV fontHeader fontBody cv)
      putStrLn "cv.pdf generated."

-------------------------------------------------------------------------------
-- End cv.hs
-------------------------------------------------------------------------------
