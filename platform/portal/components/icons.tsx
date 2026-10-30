// The one place icons come from: lucide, under the names the Portal already uses. Import icons from here only.
import {
  ArchiveRestore, ArrowLeft, CodeXml, ArrowUp, Bell, BookOpen, Check, ChevronDown, ChevronRight, Clock, Copy, CornerDownRight, Download, File, FilePen,
  Folder, FolderInput, FolderPlus, GitBranch, Globe, History, Image, LayoutGrid, LayoutTemplate, Library, Lightbulb, Link, ListPlus, LogOut,
  MessageCircleQuestion, MessageSquare, MoreHorizontal, PanelLeft, PanelRight, Pencil, Pin, PinOff, Plus, Presentation, Printer, RotateCcw,
  Search, Send, Settings, Share2, Shield, Sheet, Sparkles, Square, SquarePen, Star, BarChart3, Terminal, Trash2, Upload, Users, X,
  type LucideIcon,
} from "lucide-react";

type Props = { size?: number; filled?: boolean; className?: string };

const icon = (Glyph: LucideIcon, strokeWidth = 1.8) =>
  function Icon({ size = 18, filled, className }: Props) {
    return <Glyph size={size} strokeWidth={strokeWidth} fill={filled ? "currentColor" : "none"} className={className} aria-hidden="true" />;
  };

export const IconUpload = icon(Upload);
export const IconFile = icon(File);
export const IconDownload = icon(Download);
export const IconArrowLeft = icon(ArrowLeft);
export const IconSend = icon(Send);
export const IconNewChat = icon(SquarePen);
export const IconFolder = icon(Folder);
export const IconPanel = icon(PanelLeft);
export const IconPanelRight = icon(PanelRight);
export const IconLogOut = icon(LogOut);
export const IconSettings = icon(Settings);
export const IconMore = icon(MoreHorizontal);
export const IconSlides = icon(Presentation);
export const IconSheet = icon(Sheet);
export const IconChart = icon(BarChart3);
export const IconSparkles = icon(Sparkles);
export const IconLibrary = icon(Library);
export const IconStar = icon(Star);
export const IconMessage = icon(MessageSquare);
export const IconSearch = icon(Search);
export const IconTrash = icon(Trash2);
export const IconX = icon(X, 2);
export const IconCheck = icon(Check, 2.2);
export const IconPlus = icon(Plus, 2);
export const IconImage = icon(Image);
export const IconFolderPlus = icon(FolderPlus);
export const IconFolderInput = icon(FolderInput);
export const IconPencil = icon(Pencil);
export const IconChevronRight = icon(ChevronRight, 2);
export const IconRestore = icon(ArchiveRestore);
export const IconHistory = icon(History);
export const IconShield = icon(Shield);
export const IconCopy = icon(Copy);
export const IconRetry = icon(RotateCcw);
export const IconCode = icon(CodeXml);
export const IconBranch = icon(GitBranch);
export const IconGlobe = icon(Globe);
export const IconTerminal = icon(Terminal);
export const IconBulb = icon(Lightbulb);
export const IconArrowUp = icon(ArrowUp, 2.2);
export const IconStop = ({ size = 14, className }: Props) => <Square size={size} fill="currentColor" strokeWidth={0} className={className} aria-hidden="true" />;
export const IconChevronDown = icon(ChevronDown, 2);
export const IconBook = icon(BookOpen);
export const IconFilePen = icon(FilePen);
export const IconClock = icon(Clock);
export const IconPin = icon(Pin);
export const IconPinOff = icon(PinOff);
export const IconPrinter = icon(Printer);
export const IconBell = icon(Bell);
export const IconShare = icon(Share2);
export const IconLink = icon(Link);
export const IconUsers = icon(Users);
export const IconQueue = icon(ListPlus);
export const IconCornerDownRight = icon(CornerDownRight);
export const IconAsk = icon(MessageCircleQuestion);
export const IconTemplate = icon(LayoutTemplate);
export const IconGrid = icon(LayoutGrid);
